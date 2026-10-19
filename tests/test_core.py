import copy
import importlib.machinery
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"plugin"))
import cct_core as core


class FakeAPI:
    def __init__(self,used="100.00",limit="200.00"):
        self.calls=[]
        self.used,self.limit=used,limit
    def call(self,module,function,**params):
        self.calls.append((module,function,params))
        if module=="Quota":
            return {"megabytes_used":self.used,"megabyte_limit":self.limit}
        return []
    def clear_disk_cache(self):
        return {"status":"cleared","message":"test","retry_after_seconds":300}


class CoreTests(unittest.TestCase):
    def setUp(self):
        temporary_root=ROOT/"artifacts"
        temporary_root.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(prefix="cct-test-",dir=temporary_root)
        Path(self.temp.name).resolve().relative_to(ROOT.resolve())
        self.addCleanup(self.temp.cleanup)
        self.home=Path(self.temp.name)
        self.uid=self.home.stat().st_uid
        self.account=core.Account("testuser",self.home,self.uid)
        self.api=FakeAPI()
    def file(self,relative,days=30):
        p=self.home/relative;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b"x"*4096)
        when=time.time()-days*86400;os.utime(p,(when,when));return p
    def inventory(self):
        scan=core.Scanner(self.account,self.api)
        scan.files()
        return {"items":scan.items,"warnings":scan.warnings,"partial":scan.partial}
    def scanner(self,inventory):
        return lambda *args:SimpleNamespace(scan=lambda:inventory)
    def test_quota_normalization_uses_account_limit(self):
        q=core.normalize_quota({"megabytes_used":"1536.50","megabyte_limit":"2048.00"})
        self.assertEqual(q["used_bytes"],1611137024)
        self.assertEqual(q["free_bytes"],536346624)
    def test_unlimited_quota_is_not_zero_available(self):
        q=core.normalize_quota({"megabytes_used":"5.46","megabyte_limit":"0.00"})
        self.assertIsNone(q["limit_bytes"]);self.assertIsNone(q["free_bytes"])
    def test_unknown_negative_nan_quota_rejected(self):
        for data in ({},{"megabytes_used":"NaN","megabyte_limit":"1"},{"megabytes_used":"1","megabyte_limit":"-1"}):
            with self.assertRaises(core.CleanupError):core.normalize_quota(data)
    def test_over_quota_free_clamped(self):
        q=core.normalize_quota({"megabytes_used":"300","megabyte_limit":"200"})
        self.assertEqual(q["free_bytes"],0)
    def test_api_failure_does_not_become_empty_quota(self):
        with patch.object(self.api,"call",side_effect=core.CleanupError("unavailable")):
            q=core.get_quota(self.api)
        self.assertIsNone(q["used_bytes"]);self.assertIn("error",q)
    def test_path_traversal_absolute_and_empty_rejected(self):
        for p in ("../a","/home/another/a","a/../b","a//b","./a","a\\b","a\x00b",""):
            with self.subTest(path=p),self.assertRaises(core.CleanupError):core.validate_relative(p)
    def test_scan_does_not_classify_web_source_or_live_database(self):
        for p in ("public_html/index.php","public_html/db.sql","public_html/service.log.1.php","etc/settings.log","mail/domain/u/cur/email.log"):
            st=self.file(p).stat()
            self.assertIsNone(core.classify(p,st,time.time()))
    def test_active_and_recent_logs_protected_rotated_logs_selectable(self):
        self.file("public_html/error_log");self.file("logs/error_log.1.gz",2);self.file("logs/error_log.3.gz",30)
        items={i["relative_path"]:i for i in self.inventory()["items"]}
        self.assertFalse(items["public_html/error_log"]["selectable"])
        self.assertFalse(items["logs/error_log.1.gz"]["selectable"])
        self.assertTrue(items["logs/error_log.3.gz"]["selectable"])
    def test_latest_multipart_backup_retained(self):
        self.file("backups/backup-2026-08-01.tar.gz",80)
        self.file("backups/backup-2026-10-17-files.zip",2)
        self.file("backups/backup-2026-10-17-db.sql.gz",1)
        items={i["name"]:i for i in self.inventory()["items"]}
        self.assertTrue(items["backup-2026-08-01.tar.gz"]["selectable"])
        self.assertFalse(items["backup-2026-10-17-files.zip"]["selectable"])
        self.assertFalse(items["backup-2026-10-17-db.sql.gz"]["selectable"])
    def test_partial_scan_never_enables_backups(self):
        for n in range(5):self.file(f"backups/backup-{n}.zip",30+n*10)
        scan=core.Scanner(self.account,self.api,max_items=2);scan.files()
        self.assertTrue(scan.partial);self.assertTrue(all(not i["selectable"] for i in scan.items))
    def test_hardlink_not_counted_as_reclaimable(self):
        original=self.file(".trash/file.zip")
        os.link(original,self.home/"hardlink.zip")
        item=self.inventory()["items"][0]
        self.assertFalse(item["selectable"]);self.assertEqual(item["reclaimable_bytes"],0)
    def test_sparse_file_estimate_uses_allocated_blocks(self):
        st=SimpleNamespace(st_size=1024**3,st_blocks=8)
        self.assertEqual(core.allocated(st),4096)
    def test_exact_confirmation_required(self):
        for confirm in (None,"eliminar","ELIMINAR "):
            with self.assertRaises(core.CleanupError):core.delete_selected(self.account,self.api,[{}],confirm)
        self.assertFalse(self.api.calls)
    def test_duplicate_and_large_batches_rejected(self):
        for items in ([{"id":"a"},{"id":"a"}],[{"id":str(n)} for n in range(251)],[]):
            with self.assertRaises(core.CleanupError):core.delete_selected(self.account,self.api,items,"ELIMINAR")
    def test_changed_file_and_forged_path_not_deleted(self):
        self.file(".trash/file.zip")
        inventory=self.inventory();original=inventory["items"][0]
        for key,value in (("fingerprint",[1,2,3]),("relative_path","../other/file.zip")):
            forged=copy.deepcopy(original);forged[key]=value
            with patch.object(core,"unlink_candidate") as unlink:
                result=core.delete_selected(self.account,self.api,[forged],"ELIMINAR",scanner_factory=self.scanner(inventory))
                unlink.assert_not_called();self.assertEqual(len(result["failed"]),1)
    def test_backup_requires_separate_acknowledgement(self):
        self.file("backups/old.zip",80);self.file("backups/new.zip",1)
        inventory=self.inventory();item=next(i for i in inventory["items"] if i["selectable"])
        with patch.object(core,"unlink_candidate") as unlink:
            result=core.delete_selected(self.account,self.api,[item],"ELIMINAR",scanner_factory=self.scanner(inventory))
            unlink.assert_not_called();self.assertEqual(len(result["failed"]),1)
    def test_partial_failure_only_counts_verified_deleted_items(self):
        self.file(".trash/yes.zip");self.file(".trash/no.zip")
        inventory=self.inventory()
        def unlink(_,item):
            if item["name"]=="no.zip":raise PermissionError("denied")
        with patch.object(core,"unlink_candidate",side_effect=unlink):
            result=core.delete_selected(self.account,self.api,inventory["items"],"ELIMINAR",scanner_factory=self.scanner(inventory))
        self.assertEqual(len(result["deleted_ids"]),1);self.assertEqual(len(result["failed"]),1)
        self.assertEqual(result["observed_quota_delta_bytes"],0)
        self.assertGreater(result["estimated_removed_bytes"],0)
    def test_mail_deletion_uses_exact_uids_and_never_unlink(self):
        p=self.file("mail/domain/u/.Trash/cur/message")
        item=core.make_item(self.account,p.relative_to(self.home).as_posix(),p.stat(),"mail_trash","review","mail",account="u@domain",mailbox_guid="a"*32,uid=123,uid_validity="42")
        inventory={"items":[item],"warnings":[]}
        with patch.object(core,"unlink_candidate") as unlink,patch.object(core,"safe_stat",side_effect=[p.stat(),FileNotFoundError()]):
            result=core.delete_selected(self.account,self.api,[item],"ELIMINAR",scanner_factory=self.scanner(inventory))
        unlink.assert_not_called();self.assertEqual(result["deleted_ids"],[item["id"]])
        self.assertIn(("Mailboxes","expunge_messages_for_mailbox_guid",{"account":"u@domain","mailbox_guid":"a"*32,"query":"UID 123"}),self.api.calls)
    def test_unverified_mail_api_success_not_counted_as_recovered(self):
        p=self.file("mail/domain/u/.Trash/cur/message")
        item=core.make_item(self.account,p.relative_to(self.home).as_posix(),p.stat(),"mail_trash","review","mail",account="u@domain",mailbox_guid="a"*32,uid=123,uid_validity="42")
        with patch.object(core,"safe_stat",return_value=p.stat()):
            result=core.delete_selected(self.account,self.api,[item],"ELIMINAR",scanner_factory=self.scanner({"items":[item],"warnings":[]}))
        self.assertEqual(result["deleted_ids"],[]);self.assertEqual(result["estimated_removed_bytes"],0)
    def test_uapi_arguments_not_interpreted_by_shell(self):
        reply=SimpleNamespace(returncode=0,stdout='{"result":{"status":1,"data":[]}}')
        with patch.object(core.subprocess,"run",return_value=reply) as run:
            core.UAPI(self.account).call("Mailboxes","get_mailbox_status_list",account="user+test@domain")
        args=run.call_args.args[0]
        self.assertIn("account=user+test@domain",args);self.assertNotIn("shell",run.call_args.kwargs)
    @unittest.skipUnless(os.name=="posix","Real descriptor-based deletion requires Linux")
    def test_linux_real_delete_and_symlink_escape(self):
        p=self.file(".trash/target.zip")
        outside=self.home.parent/(self.home.name+"-outside")
        outside.mkdir();self.addCleanup(outside.rmdir)
        external=outside/"private.log.1.gz";external.write_text("do not touch")
        self.addCleanup(external.unlink)
        (self.home/"escape").symlink_to(outside,target_is_directory=True)
        with self.assertRaises(OSError):core.safe_stat(self.account,"escape/private.log.1.gz")
        item=self.inventory()["items"][0]
        core.unlink_candidate(self.account,item)
        self.assertFalse(p.exists());self.assertTrue(external.exists())
    def test_cgi_rejects_cross_origin_and_non_json_requests(self):
        loader=importlib.machinery.SourceFileLoader("cct_entry",str(ROOT/"plugin/index.live.cgi"))
        spec=importlib.util.spec_from_loader(loader.name,loader);entry=importlib.util.module_from_spec(spec);loader.exec_module(entry)
        good={"REQUEST_METHOD":"POST","CONTENT_TYPE":"application/json","HTTP_X_CCT_REQUEST":"1","HTTP_HOST":"server:2083","SERVER_PORT":"2083","HTTP_ORIGIN":"https://server:2083"}
        entry.check_post(good)
        for key,value in (("HTTP_ORIGIN","https://evil.example"),("HTTP_X_CCT_REQUEST",""),("REQUEST_METHOD","GET"),("CONTENT_TYPE","text/plain"),("HTTP_SEC_FETCH_SITE","cross-site")):
            env={**good,key:value}
            with self.assertRaises(core.CleanupError):entry.check_post(env)
    def test_cache_invalidation_status_does_not_claim_recalculation(self):
        for status,expected in ((1,"cleared"),(0,"pending")):
            reply=SimpleNamespace(returncode=0,stdout=json.dumps({"cpanelresult":{"event":{"result":1},"data":[{"status":status}]}}))
            with patch.object(core.subprocess,"run",return_value=reply):
                result=core.UAPI(self.account).clear_disk_cache()
            self.assertEqual(result["status"],expected)
            self.assertFalse(result["retry_is_guaranteed"])
    def test_failed_cache_refresh_keeps_disclaimer(self):
        with patch.object(core.subprocess,"run",side_effect=FileNotFoundError()):
            result=core.UAPI(self.account).clear_disk_cache()
        self.assertEqual(result["status"],"unavailable")
        self.assertIn("no reflejar",result["message"])
    @unittest.skipUnless(os.name!="posix","Windows-specific fail-closed check")
    def test_production_context_fails_closed_on_windows(self):
        with self.assertRaises(core.CleanupError):core.Account.current()


if __name__=="__main__":unittest.main()
