"""The steps of a chat answer: the model's `step_label` and the stored steps.

Synthetic data only. The chat's tool schemas carry an optional `step_label` that is
taken out of the arguments before a tool runs; stored tool calls and results
come back with the answer they led to when the history is loaded.
"""
import json
import os
import unittest

from tests.test_mcp import _reset_db


class ChatStepTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ['LAMBDA_ERP_DB'] = _reset_db()
        os.environ['LAMBDA_ERP_AUTO_DEMO'] = '0'
        os.environ['LAMBDA_ERP_PLUGINS'] = ''
        os.environ.setdefault('JWT_SECRET_KEY', 'chat-steps-tests-only')
        os.environ.setdefault('OPENAI_API_KEY', 'not-used')
        from fastapi.testclient import TestClient
        from api.main import app
        from lambda_erp.database import get_db
        cls.client = TestClient(app)
        cls.client.__enter__()
        cls.db = get_db()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def _session(self, sid):
        self.db.sql('DELETE FROM "Chat Message" WHERE session_id = ?', [sid])
        self.db.sql('DELETE FROM "Chat Session" WHERE id = ?', [sid])
        self.db.sql('INSERT INTO "Chat Session" (id, user_id) VALUES (?, ?)', [sid, 'anna'])
        self.db.commit()

    def _add(self, sid, role, content, message_type='chat'):
        if not isinstance(content, str):
            content = json.dumps(content)
        self.db.sql('INSERT INTO "Chat Message" (session_id, role, message_type, content) VALUES (?, ?, ?, ?)',
                    [sid, role, message_type, content])
        self.db.commit()

    def test_intent_is_offered_on_every_tool_and_never_reaches_one(self):
        from api import chat
        before = json.dumps(chat.TOOLS, sort_keys=True)
        tools = chat._with_intent(chat.build_tools({'role': 'admin', 'name': 'x'}, native_chat=True))
        self.assertEqual(json.dumps(chat.TOOLS, sort_keys=True), before, 'the shared tool list must not change')
        functions = [t for t in tools if t.get('type') == 'function']
        self.assertTrue(functions)
        for tool in functions:
            props = tool['function']['parameters']['properties']
            self.assertIn('step_label', props, tool['function']['name'])
            self.assertNotIn('step_label', tool['function']['parameters'].get('required', []))
        # No tool has a parameter of its own by that name (it is taken out of every call).
        for tool in chat.build_tools({'role': 'admin', 'name': 'x'}, native_chat=True):
            own = ((tool.get('function') or {}).get('parameters') or {}).get('properties') or {}
            self.assertNotIn('step_label', own, tool['function']['name'])
        # A tool's own `intent` parameter (analytics reports) is left alone.
        analytics = next(t for t in tools if t['function']['name'] == 'create_custom_analytics_report')
        self.assertNotEqual(analytics['function']['parameters']['properties']['intent'].get('maxLength'), chat.INTENT_MAX)
        args = {'doctype': 'sales-invoice', 'intent': 'kept', 'step_label': '  Versand als   eigene Position  '}
        self.assertEqual(chat._pop_intent(args), 'Versand als eigene Position')
        self.assertEqual(args, {'doctype': 'sales-invoice', 'intent': 'kept'})
        self.assertIsNone(chat._pop_intent({'step_label': 42}))
        self.assertEqual(len(chat._pop_intent({'step_label': 'x' * 500})), chat.INTENT_MAX)
        self.assertIn('step_label', chat.build_system_prompt({'role': 'admin', 'name': 'x'}))

    def test_history_returns_each_answers_steps(self):
        from api import chat
        sid = 'steps-1'
        self._session(sid)
        self._add(sid, 'user', 'Change SINV-1')
        self._add(sid, 'tool', {'tool': 'get_document', 'args': {'doctype': 'sales-invoice', 'name': 'SINV-1'},
                                'intent': 'Rechnung ansehen'}, 'tool_call')
        self._add(sid, 'tool', {'type': 'tool_result', 'tool': 'get_document', 'success': True,
                                'summary': '{"name": "SINV-1"}', 'result': {'name': 'SINV-1'}}, 'tool_result')
        self._add(sid, 'tool', {'tool': 'update_document', 'args': {'doctype': 'sales-invoice', 'name': 'SINV-1'}}, 'tool_call')
        self._add(sid, 'tool', {'type': 'tool_result', 'tool': 'update_document', 'success': False,
                                'summary': 'x' * 900, 'error': 'Submitted documents cannot change'}, 'tool_result')
        self._add(sid, 'assistant', 'Could not change it.')
        self._add(sid, 'user', 'Thanks')
        self._add(sid, 'assistant', 'You are welcome.')
        page = chat.load_serialized_chat_history(sid, limit=20)
        answers = [m for m in page['messages'] if m['role'] == 'assistant']
        steps = answers[0]['steps']
        self.assertEqual([s['tool'] for s in steps], ['get_document', 'update_document'])
        self.assertEqual(steps[0]['intent'], 'Rechnung ansehen')
        self.assertTrue(steps[0]['success'])
        self.assertEqual(steps[0]['args'], {'doctype': 'sales-invoice', 'name': 'SINV-1'})
        self.assertFalse(steps[1]['success'])
        self.assertEqual(steps[1]['error'], 'Submitted documents cannot change')
        self.assertLessEqual(len(steps[1]['summary']), chat.STEP_TEXT_MAX + 3)
        self.assertNotIn('result', steps[0], 'the full stored result is not sent')
        self.assertNotIn('steps', answers[1], 'an answer without tool calls has no steps')
        self.assertTrue(all('steps' not in m for m in page['messages'] if m['role'] == 'user'))
        # Steps never reach the conversation the model is given.
        convo = chat.build_conversation(sid)
        self.assertEqual([m['role'] for m in convo], ['user', 'assistant', 'user', 'assistant'])

    def test_a_page_starting_with_an_answer_still_gets_its_steps(self):
        from api import chat
        sid = 'steps-2'
        self._session(sid)
        for i in range(3):
            self._add(sid, 'user', f'question {i}')
            self._add(sid, 'tool', {'tool': 'list_documents', 'args': {'doctype': 'quotation'}}, 'tool_call')
            self._add(sid, 'tool', {'type': 'tool_result', 'tool': 'list_documents', 'success': True, 'summary': '[]'}, 'tool_result')
            self._add(sid, 'assistant', f'answer {i}')
        page = chat.load_serialized_chat_history(sid, limit=3)  # answer 1, question 2, answer 2
        self.assertEqual(page['messages'][0]['content'], 'answer 1')
        self.assertEqual(len(page['messages'][0]['steps']), 1)
        self.assertEqual(len(page['messages'][-1]['steps']), 1)

    def test_an_unanswered_turn_does_not_lend_its_steps_to_the_next(self):
        from api import chat
        sid = 'steps-3'
        self._session(sid)
        self._add(sid, 'user', 'first')
        self._add(sid, 'tool', {'tool': 'get_report', 'args': {}}, 'tool_call')  # turn broke off here
        self._add(sid, 'user', 'second')
        self._add(sid, 'assistant', 'done')
        page = chat.load_serialized_chat_history(sid, limit=20)
        self.assertNotIn('steps', page['messages'][-1])
        # A broken trace row does not cost the history.
        self._add(sid, 'tool', 'not json', 'tool_call')
        self._add(sid, 'assistant', 'still fine')
        page = chat.load_serialized_chat_history(sid, limit=20)
        self.assertEqual(page['messages'][-1]['content'], 'still fine')

    def test_a_real_turn_keeps_the_label_away_from_the_tool_and_stores_it(self):
        import asyncio
        from types import SimpleNamespace as NS
        from unittest.mock import patch
        from api import chat
        sid = 'steps-live'
        self._session(sid)
        seen_args, seen_tools = [], []
        responses = iter([
            (NS(content='', tool_calls=[NS(id='1', function=NS(
                name='get_current_time', arguments=json.dumps({'step_label': 'Datum prüfen'})))]), None),
            (NS(content='Heute ist Dienstag.', tool_calls=[]), None),
        ])

        def turn(client, messages, tools, max_tokens, **kwargs):
            seen_tools.append(tools)
            return next(responses)

        events = []

        async def event(payload):
            events.append(payload)

        async def inline_thread(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        async def run():
            with patch.object(chat, 'OpenAI', return_value=NS()), \
                 patch.object(chat, '_orchestrator_turn', side_effect=turn), \
                 patch.object(chat.asyncio, 'to_thread', new=inline_thread), \
                 patch.object(chat.demo_limiter, 'settle'), \
                 patch.object(chat.demo_limiter, 'reserve', return_value=(None, None)):
                await chat.run_thinking_loop([{'role': 'user', 'content': 'Welcher Tag?'}], event, session_id=sid,
                                             user_info={'name': 'anna', 'role': 'admin'}, max_iterations=3)
        chat.save_chat_message(sid, 'user', 'Welcher Tag?')
        asyncio.run(run())
        offered = {t['function']['name']: t for t in seen_tools[0] if t.get('type') == 'function'}
        self.assertIn('step_label', offered['get_current_time']['function']['parameters']['properties'])
        call = next(e for e in events if e.get('type') == 'tool_call')
        self.assertEqual(call['intent'], 'Datum prüfen')
        self.assertNotIn('step_label', call['args'])
        result = next(e for e in events if e.get('type') == 'tool_result')
        self.assertTrue(result['success'], result)
        # The question and answer are saved by the chat endpoint, not the loop.
        chat.save_chat_message(sid, 'assistant', 'Heute ist Dienstag.')
        steps = [m for m in chat.load_serialized_chat_history(sid)['messages'] if m['role'] == 'assistant'][0]['steps']
        self.assertEqual(steps, [{'tool': 'get_current_time', 'intent': 'Datum prüfen', 'args': {},
                                  'success': True, 'summary': steps[0]['summary']}])


if __name__ == '__main__':
    unittest.main(verbosity=2)
