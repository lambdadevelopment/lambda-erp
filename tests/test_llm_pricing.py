"""Regression checks for model billing and the Luna tool-call request contract.

Run with python -m tests.test_llm_pricing; no external API calls are made.
"""
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from api.providers import cost_of_openai_call, get_openai_rates


class PricingTests(unittest.TestCase):
    def test_standard_usage_both_api_shapes(self):
        # 10K input: 6K normal + 4K cached; 2K output, including reasoning.
        expected = {"gpt-6-astra": .164, "gpt-6-sol": .0328,
                    "gpt-6-luna": .00164, "gpt-6.1-sol": .0324,
                    "gpt-5.6-terra": .0368}
        for model, dollars in expected.items():
            for usage in (
                NS(prompt_tokens=10_000, completion_tokens=2_000,
                   prompt_tokens_details=NS(cached_tokens=4_000)),
                NS(input_tokens=10_000, output_tokens=2_000,
                   input_tokens_details=NS(cached_tokens=4_000)),
            ):
                with self.subTest(model=model, usage=usage):
                    self.assertAlmostEqual(cost_of_openai_call(model, usage), dollars)

    def test_long_context_charges_entire_request_including_cache(self):
        # Most input is cached, but the TOTAL input still crosses the boundary.
        usage = NS(input_tokens=300_000, output_tokens=1_000,
                   input_tokens_details=NS(cached_tokens=290_000))
        expected = {"gpt-6-astra": .855, "gpt-6-sol": .171,
                    "gpt-6-luna": .00855, "gpt-6.1-sol": .113,
                    "gpt-5.6-terra": .174}
        for model, dollars in expected.items():
            with self.subTest(model=model):
                self.assertAlmostEqual(cost_of_openai_call(model, usage), dollars)
                short = get_openai_rates(model, 272_000)
                long = get_openai_rates(model, 272_001)
                for key in ("input", "cached_input", "cache_write"):
                    self.assertEqual(long[key], short[key] * 2)
                self.assertEqual(long["output"], short["output"] * 1.5)

    def test_missing_usage_and_uncached_usage(self):
        self.assertEqual(cost_of_openai_call("gpt-6-luna", None), 0)
        self.assertAlmostEqual(cost_of_openai_call(
            "gpt-6-luna", NS(input_tokens=1_000, output_tokens=100)), .00015)


class OrchestratorTests(unittest.TestCase):
    def test_luna_responses_tool_roundtrip_and_usage(self):
        from api import chat
        messages = [{"role": "system", "content": "Use ERP tools."},
                    {"role": "user", "content": "List quotations."}]
        tool = {"type": "function", "function": {"name": "list_documents",
                "description": "List ERP documents", "parameters": {
                    "type": "object", "properties": {"doctype": {"type": "string"}},
                    "required": ["doctype"]}}}
        response = NS(output=[NS(type="function_call", call_id="call_test",
                      name="list_documents", arguments='{"doctype":"quotation"}')],
                      usage=NS(input_tokens=10_000, output_tokens=2_000,
                               input_tokens_details=NS(cached_tokens=4_000)))
        create = Mock(return_value=response)
        client = NS(responses=NS(create=create))
        with patch.dict("os.environ", {"ERP_CHAT_API": "responses"}):
            message, usage = chat._orchestrator_turn(client, messages, [tool], 1024)
        request = create.call_args.kwargs
        self.assertEqual(request["model"], "gpt-6-luna")
        self.assertEqual(request["reasoning"], {"effort": "low"})
        self.assertEqual(request["tools"][0]["name"], "list_documents")
        self.assertEqual(message.tool_calls[0].function.name, "list_documents")
        self.assertEqual(message.tool_calls[0].id, "call_test")
        self.assertAlmostEqual(cost_of_openai_call(request["model"], usage), .00164)

    def test_chat_completions_compatibility_uses_none(self):
        from api import chat
        create = Mock(return_value=NS(choices=[NS(message=NS(content="ok"))], usage=None))
        client = NS(chat=NS(completions=NS(create=create)))
        with patch.dict("os.environ", {"ERP_CHAT_API": "chat"}):
            chat._orchestrator_turn(client, [{"role": "user", "content": "Hi"}], [], 1024)
        self.assertEqual(create.call_args.kwargs["model"], "gpt-6-luna")
        self.assertEqual(create.call_args.kwargs["reasoning_effort"], "none")


if __name__ == "__main__":
    unittest.main()
