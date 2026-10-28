"""Regression coverage for the LiveAPI handshake and uncontaminated JSON."""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugin'))
import cct_core as core
from cct_live import PipeTransport


class TransportTests(unittest.TestCase):
    def test_calls_are_framed_without_shell_or_cli(self):
        output = io.StringIO()
        source = io.StringIO(json.dumps({'type': 'api_result', 'ok': True, 'document': {'result': {'status': 1, 'data': []}}}) + '\n')
        api = core.UAPI(None, PipeTransport(source, output))
        self.assertEqual(api.call('Email', 'list_pops', name='value\nwith quotes"'), [])
        self.assertEqual(json.loads(output.getvalue())['params'], {'name': 'value\nwith quotes"'})

    def test_liveapi_error_appended_to_json_is_never_silently_ignored(self):
        for value in ['', '{}', '[]\n', '{}Child failed to make LIVEAPI connection to cPanel.\n']:
            with self.subTest(value=value), self.assertRaises(core.CleanupError):
                PipeTransport(io.StringIO(value), io.StringIO()).read_frame()

    def test_uapi_accepts_documented_and_wrapped_liveapi_results(self):
        result = {'status': 1, 'data': {'megabytes_used': '100', 'megabyte_limit': '200'}}
        for document in [result, {'result': result}, {'cpanelresult': {'result': result}}, {'cpanelresult': result}]:
            transport = Mock(); transport.call.return_value = document
            self.assertEqual(core.UAPI(None, transport).call('Quota', 'get_quota_info'), result['data'])

    def test_disabled_feature_fails_closed(self):
        transport = Mock(); transport.call.return_value = {'cpanelresult': {'result': {'status': 0, 'errors': ['Feature disabled']}}}
        with self.assertRaises(core.CleanupError):
            core.UAPI(None, transport).require_feature()
        self.assertEqual(transport.call.call_count, 1)

    def test_no_implicit_fallback_to_command_line(self):
        with self.assertRaises(core.CleanupError):
            core.UAPI(None).require_feature()


STUB = r'''
package Cpanel::LiveAPI;
use strict;
use warnings;
sub log_event { open my $f, '>>', $ENV{CCT_TEST_LOG} or die $!; print {$f} $_[0], "\n"; close $f; }
sub new { log_event('new'); print "diagnostic during connect\n"; return bless {}, shift; }
sub uapi {
    my ($self, $module, $function, $params) = @_;
    log_event("$module/$function");
    die 'Simulated vendor exception' if ($ENV{CCT_TEST_MODE} // '') eq 'fail';
    print "diagnostic during API call\n";
    my $disabled = ($ENV{CCT_TEST_MODE} // '') eq 'disabled';
    my $data = $module eq 'Quota' ? {megabytes_used => '100', megabyte_limit => '200'} : undef;
    return {cpanelresult => {result => {status => $disabled ? 0 : 1, data => $data, errors => $disabled ? ['Feature disabled'] : undef}}};
}
sub api2 { log_event('DiskUsage/clearcache'); return {cpanelresult => {event => {result => 1}, data => [{status => 1}]}}; }
sub end { log_event('end'); print "diagnostic during disconnect\n"; }
1;
'''


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() != 0 and shutil.which('perl'), 'Perl bridge integration runs as a non-root Linux account')
class BridgeIntegrationTests(unittest.TestCase):
    def setUp(self):
        (ROOT / 'artifacts').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='cct-liveapi-test-', dir=ROOT / 'artifacts')
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        for path in (ROOT / 'plugin').iterdir():
            if path.is_file():
                shutil.copy2(path, self.folder / path.name)
        (self.folder / 'Cpanel').mkdir()
        (self.folder / 'Cpanel/LiveAPI.pm').write_text(STUB)
        bridge = (self.folder / 'index.live.pl').read_text()
        # Replace only the vendor library location, leaving the production
        # handshake, IPC, backend, parsing, close and HTTP code intact.
        (self.folder / 'index.live.pl').write_text(bridge.replace("use lib '/usr/local/cpanel';", "use lib '" + str(self.folder) + "';"))
        (self.folder / 'python-path').write_text(sys.executable + '\n')

    def request(self, action='quota', mode='', body=b'', extra=None, content_length='auto'):
        env = {**os.environ, 'QUERY_STRING': 'action=' + action, 'REQUEST_METHOD': 'POST' if body else 'GET',
               'CONTENT_LENGTH': str(len(body)), 'CCT_TEST_LOG': str(self.folder / 'calls.log'), 'CCT_TEST_MODE': mode}
        env.pop('REMOTE_USER', None)
        env.update(extra or {})
        if content_length is None:
            env.pop('CONTENT_LENGTH', None)
        elif content_length != 'auto':
            env['CONTENT_LENGTH'] = content_length
        result = subprocess.run(['perl', str(self.folder / 'index.live.pl')], input=body, env=env, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        headers, payload = result.stdout.split(b'\r\n\r\n', 1)
        self.assertIn(b'Content-Type: ' + (b'text/html' if action == 'page' else b'application/json'), headers)
        length = int(next(line.split(b': ', 1)[1] for line in headers.split(b'\r\n') if line.startswith(b'Content-Length: ')))
        self.assertEqual(length, len(payload))
        self.assertNotIn(b'diagnostic', result.stdout)
        calls = (self.folder / 'calls.log').read_text().splitlines()
        self.assertEqual(calls.count('new'), 1)
        self.assertEqual(calls.count('end'), 1)
        return headers, payload.decode('utf-8') if action == 'page' else json.loads(payload), calls

    def test_page_get_accepts_empty_content_length_from_cpsrvd(self):
        headers, page, calls = self.request(action='page', content_length='', extra={'QUERY_STRING':'embedded=1'})
        self.assertIn(b'Status: 200', headers)
        self.assertIn('<!doctype html>', page.lower())
        self.assertEqual(calls, ['new', 'Features/has_feature', 'end'])

    def test_old_bookmarks_redirect_to_native_shell_after_feature_check(self):
        headers, page, calls = self.request(action='page', content_length='')
        self.assertIn(b'Status: 302 Found', headers)
        self.assertIn(b'Location: home.html', headers)
        self.assertEqual(page, '')
        self.assertEqual(calls, ['new', 'Features/has_feature', 'end'])

    def test_get_accepts_missing_content_length(self):
        headers, data, calls = self.request(content_length=None)
        self.assertIn(b'Status: 200', headers)
        self.assertEqual(data['quota']['limit_bytes'], 200 * 1048576)

    def test_malformed_and_oversized_content_lengths_are_rejected(self):
        for value in ['-1', '1x', ' 0', '0 ', '1048577']:
            with self.subTest(value=value):
                log = self.folder / 'calls.log'
                log.unlink(missing_ok=True)
                headers, data, calls = self.request(content_length=value)
                self.assertIn(b'Status: 500', headers)
                self.assertIn('error', data)
                self.assertEqual(calls, ['new', 'end'])

    def test_real_bridge_lifecycle_and_quota_with_stub_vendor(self):
        headers, data, calls = self.request()
        self.assertIn(b'Status: 200', headers)
        self.assertEqual(data['quota']['limit_bytes'], 200 * 1048576)
        self.assertEqual(calls, ['new', 'Features/has_feature', 'Quota/get_quota_info', 'end'])

    def test_disabled_feature_returns_one_json_error_without_quota_call(self):
        headers, data, calls = self.request(mode='disabled')
        self.assertIn(b'Status: 400', headers)
        self.assertIn('error', data)
        self.assertNotIn('Quota/get_quota_info', calls)

    def test_vendor_exception_still_closes_and_returns_one_json(self):
        headers, data, calls = self.request(mode='fail')
        self.assertIn(b'Status: 500', headers)
        self.assertIn('error', data)

    def test_post_body_and_api2_cache_use_the_same_liveapi_connection(self):
        headers, data, calls = self.request(action='refresh', body=b'{}', extra={
            'CONTENT_TYPE': 'application/json', 'HTTP_X_CCT_REQUEST': '1',
            'HTTPS': 'on', 'HTTP_HOST': 'cpanel.example.com', 'HTTP_ORIGIN': 'https://cpanel.example.com'})
        self.assertIn(b'Status: 200', headers)
        self.assertEqual(data['cache_refresh']['status'], 'cleared')
        self.assertEqual(calls, ['new', 'Features/has_feature', 'DiskUsage/clearcache', 'Quota/get_quota_info', 'end'])


if __name__ == '__main__':
    unittest.main()
