"""Opt-in model regression: proposed CRM writes after a company/topic switch.

OPENAI_API_KEY=... python -m tests.eval_chat_attribution
Uses the real core system prompt and request adapter with synthetic conversation
and lookup results. Calls OpenAI (billable), but never executes any ERP writes.
Offline CI tests the adapter separately in test_llm_pricing.
"""
import json
from openai import OpenAI
from api import chat


def run():
    client = OpenAI(timeout=90, max_retries=0)
    tool = {"type": "function", "function": {
        "name": "update_document", "description": "Update the identified lead and record the user's reply in _note.",
        "parameters": {"type": "object", "properties": {
            "doctype": {"type": "string", "enum": ["lead"]},
            "name": {"type": "string"},
            "data": {"type": "object", "properties": {
                "status": {"type": "string", "enum": ["Lost"]},
                "_note": {"type": "string"}}, "required": ["status", "_note"],
                "additionalProperties": False}},
            "required": ["doctype", "name", "data"], "additionalProperties": False}}}
    cases = [
        ("unsigned_team", "Das Korallen-Team sagt: Vielen Dank für die E-Mail. Aktuell sind wir nicht interessiert.",
         {}, ["Florian", "Keller"], None),
        ("known_contact_is_not_sender", "Das Korallen-Team sagt: Aktuell sind wir nicht interessiert.",
         {"existing_contact": "Mara Weiss"}, ["Florian", "Keller", "Mara", "Weiss"], None),
        ("explicit_sender", "Mara Weiss vom Korallen-Team schreibt: Aktuell sind wir nicht interessiert.",
         {"existing_contact": "Mara Weiss"}, ["Florian", "Keller"], "Mara Weiss"),
    ]
    for case, reply, extra, forbidden, required in cases:
        messages = [
            {"role": "system", "content": chat.build_system_prompt({"role": "admin", "full_name": "Test User"})},
            {"role": "user", "content": "Mit Florian Keller von Trainingsfirma AG findet ein Einführungsgespräch statt. Bitte im CRM notieren."},
            {"role": "assistant", "content": "Das Einführungsgespräch mit Florian Keller wurde bei Trainingsfirma AG erfasst."},
            {"role": "user", "content": "Anderer Lead: Korallen AG. " + reply + " Entsprechend im CRM updaten: kein Interesse."},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "lookup", "type": "function",
                "function": {"name": "search_masters", "arguments": '{"master_type":"lead","query":"Korallen"}'}}]},
            {"role": "tool", "tool_call_id": "lookup", "content": json.dumps([
                {"name": "LEAD-TEST-B", "company_name": "Korallen AG", "status": "Contacted", **extra}])},
        ]
        message, _ = chat._orchestrator_turn(client, messages, [tool], 2048)
        calls = message.tool_calls or []
        assert len(calls) == 1 and calls[0].function.name == "update_document", (
            case, message.content, [c.function.name for c in calls])
        args = json.loads(calls[0].function.arguments)
        assert args["doctype"] == "lead" and args["name"] == "LEAD-TEST-B", case
        assert args["data"]["status"] == "Lost", case
        note = args["data"]["_note"]
        assert note.strip() and not any(n.casefold() in note.casefold() for n in forbidden), (case, note)
        if required:
            assert required.casefold() in note.casefold(), (case, note)
        print(json.dumps({"case": case, "model": chat.ORCHESTRATOR_MODEL, "passed": True}), flush=True)


if __name__ == "__main__":
    run()
