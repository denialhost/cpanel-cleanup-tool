"""Cross-request scanning and selected-item safety, using disposable fixtures."""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugin'))
import cct_core as core
from cct_batch import BatchScan, possible_file, recheck_selection


class API:
    def __init__(self, messages=0):
        self.messages = messages
        self.calls = []

    def call(self, module, function, **params):
        self.calls.append((module, function, params))
        if module == 'Email':
            return [{'email': 'ventas@example.test'}] if self.messages else []
        if module == 'Mailboxes' and params['account'] == 'ventas@example.test':
            return [{'mailbox': 'INBOX.Junk', 'guid': 'a' * 32, 'messages': self.messages}]
        return []


class FilterTests(unittest.TestCase):
    def test_cheap_prefilter_never_excludes_a_recognized_file(self):
        from types import SimpleNamespace
        for relative in ('.trash/photo.jpg', 'logs/access.gz', 'public_html/error_log',
                         'public_html/sub/x.log.2.gz', 'backups/old.zip', 'backup-2020.tgz',
                         'public_html/wp-content/updraft/old.sql.gz'):
            self.assertIsNotNone(core.classify(relative, SimpleNamespace(st_mtime=0), time.time()))
            self.assertTrue(possible_file(relative), relative)
        for relative in ('public_html/photo.jpg', 'public_html/index.php', 'notes.txt'):
            self.assertFalse(possible_file(relative), relative)


@unittest.skipUnless(os.name == 'posix' and Path('/proc/self/fd').exists(), 'Account-owned batch storage requires Linux')
class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='cct-batch-test-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.home = self.base / 'home'
        self.home.mkdir()
        self.account = core.Account('testuser', self.home, self.home.stat().st_uid)
        self.api = API()
        self.directory = self.base / 'job'

    def job(self, **kwargs):
        return BatchScan(self.account, self.api, self.directory, entries=7, **kwargs)

    def file(self, relative, days=40, content=b'x'):
        path = self.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        timestamp = time.time() - days * 86400
        os.utime(path, (timestamp, timestamp))
        return path

    def start(self):
        with self.job() as job:
            return job.start()['token']

    def complete(self, token):
        for unused in range(1000):
            with self.job() as job:
                status = job.step(token)
            if status['done']:
                break
        else:
            self.fail('Analysis did not finish')
        items, offset = [], 0
        while offset is not None:
            with self.job() as job:
                result = job.results(token, offset)
            items.extend(result['items'])
            offset = result['next_offset']
        return status, items

    def test_resumes_across_requests_beyond_legacy_entry_limit_and_reaches_mail(self):
        for index in range(60):
            self.file('public_html/photos/' + str(index) + '.jpg')
        self.file('public_html/z-last/error_log.4.gz')
        with patch.object(core, 'MAX_VISITED', 5), patch.object(core, 'SCAN_SECONDS', 0):
            status, items = self.complete(self.start())
        self.assertFalse(status['partial'])
        self.assertGreater(status['files_visited'], 60)
        self.assertGreater(status['batches'], 2)
        self.assertEqual([row['relative_path'] for row in items], ['public_html/z-last/error_log.4.gz'])
        self.assertTrue(any(module == 'Mailboxes' for module, function, params in self.api.calls))

    def test_paging_returns_every_item_once(self):
        for index in range(12):
            self.file('.trash/' + str(index))
        with patch('cct_batch.PAGE_SIZE', 3):
            status, items = self.complete(self.start())
        self.assertEqual(len(items), 12)
        self.assertEqual(len({row['id'] for row in items}), 12)
        self.assertEqual(status['candidates'], 12)

    def test_non_utf8_directory_and_filenames_survive_resume_and_json_roundtrip(self):
        directory = os.fsencode(self.home) + b'/odd-\xaa'
        os.mkdir(directory)
        for index in range(15):
            name = directory + b'/error-\xff-' + str(index).encode() + b'.log'
            with open(name, 'wb') as stream:
                stream.write(b'fixture')
        status, items = self.complete(self.start())
        self.assertFalse(status['partial'], status['warnings'])
        self.assertEqual(len(items), 15)
        decoded = json.loads(json.dumps(items))
        self.assertEqual(decoded, items)
        self.assertTrue(all(core.safe_stat(self.account, item['relative_path']) for item in decoded))

    def test_time_budget_pauses_without_claiming_an_incomplete_inventory(self):
        self.file('.trash/old')
        token = self.start()
        with self.job(seconds=0) as job:
            status = job.step(token)
            self.assertFalse(status['done'])
            self.assertFalse(status['partial'])
            self.assertEqual(status['warnings'], [])
            with self.assertRaises(core.CleanupError):
                job.results(token)
        self.assertTrue(self.complete(token)[0]['done'])

    def test_new_page_attaches_to_the_active_job_instead_of_restarting_it(self):
        for index in range(20):
            self.file('public_html/' + str(index) + '.jpg')
        token = self.start()
        with self.job() as job:
            first = job.step(token)
        with self.job() as job:
            resumed = job.start()
        self.assertEqual(resumed['token'], token)
        self.assertEqual(resumed['visited'], first['visited'])
        self.assertGreater(resumed['visited'], 0)

    def test_wide_tree_keeps_only_the_active_branch_in_the_resume_queue(self):
        for index in range(100):
            self.file('public_html/wide/' + str(index) + '/photo.jpg')
        token = self.start()
        for unused in range(1000):
            with self.job() as job:
                status = job.step(token)
                queued = job.db.execute('SELECT COUNT(*) FROM dirs').fetchone()[0]
                self.assertLessEqual(queued, 4)
            if status['done']:
                break
        self.assertTrue(status['done'])
        self.assertFalse(status['partial'])

    def test_tokens_expire_and_cannot_be_reused_for_another_account_home(self):
        token = self.start()
        with self.job() as job:
            with self.assertRaises(core.CleanupError):
                job.step('b' * 64)
            other = core.Account('other', self.base, self.account.uid)
            job.account = other
            with self.assertRaises(core.CleanupError):
                job.step(token)
            job.account = self.account
            job.meta['created'] = 0
            with self.assertRaises(core.CleanupError):
                job.step(token)

    def test_job_symlinks_and_world_readable_storage_are_rejected(self):
        outside = self.base / 'outside'
        outside.write_bytes(b'keep')
        self.directory.mkdir(mode=0o700)
        (self.directory / 'scan.db').symlink_to(outside)
        with self.assertRaises(OSError), self.job():
            pass
        self.assertEqual(outside.read_bytes(), b'keep')
        (self.directory / 'scan.db').unlink()
        self.directory.chmod(0o755)
        with self.assertRaises(core.CleanupError), self.job():
            pass

    def test_symlink_directories_are_not_traversed(self):
        outside = self.base / 'outside'
        outside.mkdir()
        (outside / 'error_log.3.gz').write_bytes(b'private')
        (self.home / 'alias').symlink_to(outside, target_is_directory=True)
        status, items = self.complete(self.start())
        self.assertFalse(status['partial'])
        self.assertEqual(items, [])

    def test_backup_retention_is_computed_after_all_pages_and_rechecked_before_delete(self):
        old = self.file('backup/old.zip', days=40)
        newest = self.file('backup/new.zip', days=1)
        token = self.start()
        status, items = self.complete(token)
        picked = next(item for item in items if item['name'] == old.name)
        self.assertTrue(picked['selectable'])
        self.assertFalse(next(item for item in items if item['name'] == newest.name)['selectable'])
        newest.unlink()
        result = recheck_selection(self.account, self.api, [picked])
        self.assertFalse(result['items'][0]['selectable'])

    def test_stale_selection_and_forged_paths_cannot_be_approved(self):
        path = self.file('.trash/old')
        token = self.start()
        status, items = self.complete(token)
        altered = dict(items[0], relative_path='../outside')
        with self.job() as job:
            with self.assertRaises(core.CleanupError):
                job.approved(token, [altered])
            approved = job.approved(token, items)
        path.write_bytes(b'changed')
        result = recheck_selection(self.account, self.api, approved)
        self.assertFalse(core.same_item(items[0], result['items'][0]))

    def test_delete_rechecks_only_the_selection_and_rejects_changed_files(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        kept = self.file('.trash/changed', content=b'original')
        removed = self.file('.trash/selected')
        untouched = self.file('.trash/not-selected')
        token = self.start()
        status, items = self.complete(token)
        selected = [item for item in items if item['name'] != untouched.name]
        with self.job() as job:
            approved = job.approved(token, selected)
        kept.write_bytes(b'changed after analysis')
        api = Mock()
        api.call.return_value = {'megabytes_used': '1', 'megabyte_limit': '2'}
        api.clear_disk_cache.return_value = {'status': 'cleared'}
        factory = lambda account, api: SimpleNamespace(scan=lambda: recheck_selection(account, api, approved))
        with patch.object(core.Scanner, 'scan', side_effect=AssertionError('Full traversal must not run')):
            result = core.delete_selected(self.account, api, selected, 'ELIMINAR', scanner_factory=factory)
        self.assertFalse(removed.exists())
        self.assertEqual(kept.read_bytes(), b'changed after analysis')
        self.assertTrue(untouched.exists())
        self.assertEqual(len(result['deleted_ids']), 1)
        self.assertEqual(len(result['failed']), 1)
        api.clear_disk_cache.assert_called_once()

    def mail(self, count):
        self.api = API(count)
        lines = ['3 V42 N999 G' + 'a' * 32]
        for index in range(1, count + 1):
            self.file('mail/example.test/ventas/.Junk/cur/message-' + str(index) + ':2,S')
            lines.append(str(index) + ' :message-' + str(index))
        return self.file('mail/example.test/ventas/.Junk/dovecot-uidlist', content=('\n'.join(lines) + '\n').encode())

    def test_large_mailbox_index_and_files_continue_in_separate_batches(self):
        self.mail(40)
        status, items = self.complete(self.start())
        self.assertFalse(status['partial'], status['warnings'])
        self.assertEqual(len(items), 40)
        self.assertEqual({item['uid'] for item in items}, set(range(1, 41)))
        self.assertTrue(all(item['uid_validity'] == '42' for item in items))

    def test_mail_metadata_preserves_non_utf8_filenames(self):
        index = self.mail(1)
        folder = os.fsencode(index.parent / 'cur')
        os.rename(folder + b'/message-1:2,S', folder + b'/message-\xaa:2,S')
        index.write_bytes(('3 V42 N999 G' + 'a' * 32 + '\n').encode() + b'1 :message-\xaa\n')
        status, items = self.complete(self.start())
        self.assertFalse(status['partial'], status['warnings'])
        self.assertEqual(len(items), 1)
        self.assertEqual(len(recheck_selection(self.account, self.api, items)['items']), 1)

    def test_mail_uid_validity_or_uid_filename_changes_block_selected_messages(self):
        index = self.mail(3)
        token = self.start()
        status, items = self.complete(token)
        self.assertEqual(len(recheck_selection(self.account, self.api, items)['items']), 3)
        original = index.read_text()
        index.write_text(original.replace('V42', 'V43'))
        self.assertEqual(recheck_selection(self.account, self.api, items)['items'], [])
        index.write_text(original.replace(':message-1', ':different-name'))
        checked = recheck_selection(self.account, self.api, items)['items']
        self.assertEqual(len(checked), 2)


if __name__ == '__main__':
    unittest.main()
