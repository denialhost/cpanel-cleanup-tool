import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugin'))
import cct_audit as audit
import cct_core as core


@unittest.skipUnless(os.name == 'posix', 'Audit filesystem checks require POSIX')
class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cct-audit-')
        self.home = Path(self.temp.name)
        self.account = core.Account('owner', self.home, os.getuid())

    def tearDown(self):
        self.temp.cleanup()

    def test_append_and_read_monthly_jsonl(self):
        result = audit.append_events(self.account, [
            {'id': 'abc', 'category': 'trash', 'relative_path': '.trash/a', 'bytes': 10, 'result': 'deleted'},
            {'id': 'def', 'category': 'spam', 'relative_path': 'mail/x', 'bytes': 20, 'result': 'failed', 'error': 'busy'},
        ], actor='cpanel:owner', via='cpanel')
        self.assertEqual(result['wrote'], 2)
        month = audit._month_name()
        self.assertEqual(audit.list_months(self.account), [month])
        payload = audit.read_month(self.account, month)
        self.assertEqual(len(payload['events']), 2)
        self.assertEqual(payload['events'][0]['actor'], 'cpanel:owner')
        self.assertEqual(payload['events'][1]['result'], 'failed')
        mode = (self.home / '.cct' / 'audit').stat().st_mode & 0o777
        self.assertEqual(mode, 0o700)

    def test_rejects_symlink_audit_root(self):
        outside = Path(tempfile.mkdtemp(prefix='cct-audit-out-'))
        self.addCleanup(lambda: os.rmdir(outside) if outside.exists() else None)
        (self.home / '.cct').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(core.CleanupError):
            audit.append_events(self.account, [{'id': 'x', 'result': 'deleted'}], actor='root', via='whm')

    @unittest.skipUnless(hasattr(os, 'O_NOFOLLOW'), 'Deletion and audit require O_NOFOLLOW')
    def test_delete_selected_writes_audit_events(self):
        target = self.home / '.trash' / 'old.zip'
        target.parent.mkdir()
        target.write_bytes(b'x' * 100)
        item = core.make_item(self.account, '.trash/old.zip', target.stat(), 'trash', 'review', 'trash')
        api = mock.Mock()
        api.refresh_disk_usage.return_value = {'status': 'built'}
        factory = lambda account, client: type('S', (), {'scan': lambda self: {'items': [item], 'warnings': []}})()
        with patch.object(core, 'get_quota', return_value={'used_bytes': 100, 'limit_bytes': 200}):
            result = core.delete_selected(self.account, api, [item], 'ELIMINAR', scanner_factory=factory,
                                         actor='cpanel:owner', via='cpanel')
        self.assertEqual(result['deleted_ids'], [item['id']])
        events = audit.read_month(self.account)['events']
        self.assertTrue(any(row['id'] == item['id'] and row['result'] == 'deleted' for row in events))


if __name__ == '__main__':
    unittest.main()
