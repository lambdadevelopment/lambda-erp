"""Live extension discovery for a direct client, without a database or LLM."""
import unittest
from unittest.mock import patch

from api import services
from api.chat import build_tools
from api.erp_context import get_erp_context, WORKFLOW_GUIDANCE
from api.pdf_profiles import PROFILES, DisabledPDF
from api.routers.mcp import _handle, _tools


class ContextTests(unittest.TestCase):
    def test_registered_types_and_actions_are_discovered_without_connector_changes(self):
        registries = [services.SLUG_TO_DOCTYPE, services.DOCTYPE_TO_SLUG,
                      services.DOCUMENT_CLASSES, services.MASTER_TABLES,
                      services.MASTER_METADATA, services.CHAT_DOCTYPES,
                      services.REGISTERED_ACTIONS, PROFILES]
        from contextlib import ExitStack
        with ExitStack() as stack:
            for registry in registries:
                stack.enter_context(patch.dict(registry))
            services.register_doctype('Inspection Visit', type('Visit', (), {'LINK_FIELDS': {'customer': 'Customer'}}),
                                      pdf_profile=DisabledPDF('Internal record'))
            services.register_chat_doctype('inspection-visit', description='A customer inspection', fields=['customer'])
            services.register_master('probe-brand', 'Probe Brand', 'brand_name', description='Equipment brand')
            services.register_action('record_inspection', lambda args: {'ok': True}, description='Record inspection')
            context = get_erp_context()
            visit = next(d for d in context['documents'] if d['type'] == 'inspection-visit')
            self.assertEqual(visit['links'], {'customer': 'Customer'})
            self.assertEqual(visit['description'], 'A customer inspection')
            self.assertFalse(visit['pdf']['supported'])
            self.assertEqual(next(m for m in context['masters'] if m['type'] == 'probe-brand')['display_field'], 'brand_name')
            manager = {t['name']: t for t in _tools('manager')}
            viewer = {t['name']: t for t in _tools('viewer')}
            self.assertIn('record_inspection', manager)
            self.assertNotIn('record_inspection', viewer)
            self.assertIn('inspection-visit', manager['create_document']['inputSchema']['properties']['doctype']['enum'])
            self.assertIn('probe-brand', manager['search_masters']['inputSchema']['properties']['master_type']['enum'])

    def test_context_is_read_only_and_shared_with_chat(self):
        tool = next(t for t in _tools('viewer') if t['name'] == 'get_erp_context')
        self.assertTrue(tool['annotations']['readOnlyHint'])
        self.assertIn('get_erp_context', {t['function']['name'] for t in build_tools({'role': 'viewer'})})
        result = _handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                          'params': {'name': 'get_erp_context', 'arguments': {}}}, {'role': 'viewer'})
        self.assertFalse(result['result']['isError'])
        self.assertEqual(get_erp_context()['instructions'], WORKFLOW_GUIDANCE)
        self.assertIn('timeout', WORKFLOW_GUIDANCE)


if __name__ == '__main__':
    unittest.main()
