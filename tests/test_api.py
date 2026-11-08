import io
import json
import unittest
from unittest.mock import patch

from ganss.api import dispatch, main
from ganss.runner import ProtocolError


class ApiTests(unittest.TestCase):
    def request(self, method, params=None):
        return {'api_version': 1, 'id': 'test', 'method': method, 'params': params or {}}

    def test_scan_filters_through_existing_scanner(self):
        with patch('ganss.api.scan_hardware', return_value=[{'name': 'GS3104T-PRO 2.4G', 'endpoints': []}]):
            response = dispatch(self.request('devices.scan'))
        self.assertEqual(response['result']['devices'], [{'name': 'GS3104T-PRO 2.4G'}])
        self.assertEqual(response['id'], 'test')

    def test_invalid_requests(self):
        for request in ([], {}, self.request('unknown'), self.request('keys.list', {'typo': 1}),
                        self.request('lighting.set', {'device': 'test', 'dry_run': 'false'})):
            self.assertFalse(dispatch(request)['ok'])

    def test_dry_run_never_opens_device(self):
        with patch('ganss.api.select_hardware', return_value={'name': 'keyboard'}), \
             patch('ganss.api.select_transport', return_value=('/dev/example', 'wireless')), \
             patch('ganss.api.Hidraw') as transport:
            response = dispatch(self.request('keymap.set', {
                'device': 'keyboard', 'source': 'A', 'target': 'B', 'dry_run': True}))
        transport.assert_not_called()
        self.assertEqual(response['result']['status'], 'planned')
        self.assertEqual(len(response['result']['steps']), 1)

    def test_write_reports_transport_only(self):
        with patch('ganss.api.select_hardware', return_value={'name': 'keyboard'}), \
             patch('ganss.api.select_transport', return_value=('/dev/example', 'wireless')), \
             patch('ganss.api.Hidraw'), patch('ganss.api.execute', return_value=1):
            response = dispatch(self.request('lighting.set', {'device': 'keyboard'}))
        self.assertEqual(response['result']['completed_steps'], 1)
        self.assertFalse(response['result']['effect_verified'])

    def test_partial_failure(self):
        with patch('ganss.api.select_hardware', return_value={'name': 'keyboard'}), \
             patch('ganss.api.select_transport', return_value=('/dev/example', 'wireless')), \
             patch('ganss.api.Hidraw'), patch('ganss.api.execute', side_effect=ProtocolError('timeout')):
            response = dispatch(self.request('lighting.set', {'device': 'keyboard'}))
        self.assertEqual(response['error']['code'], 'transfer_failed')
        self.assertTrue(response['error']['may_have_partial_changes'])

    def test_json_lines_recovers_after_bad_request(self):
        lines = '{bad}\n{"api_version":1,"method":"keys.list"}\n'
        output = io.StringIO()
        with patch('sys.stdin', io.StringIO(lines)), patch('sys.stdout', output):
            main()
        responses = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(responses), 2)
        self.assertEqual(responses[0]['error']['code'], 'invalid_json')
        self.assertTrue(responses[1]['ok'])
