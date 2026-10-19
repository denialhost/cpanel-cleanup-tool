import copy
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'plugin'))
import cct_core as core
import cct_mail as mail


class API:
    def __init__(self):self.calls=[]
    def call(self,module,function,**params):
        self.calls.append((module,function,params))
        if function=='list_pops':return [{'email':'ventas@example.com'},{'email':'contacto@example.com'}]
        if module=='Quota':return {'megabytes_used':'100','megabyte_limit':'200'}
        if function=='get_mailbox_status_list':return [
            {'mailbox':'INBOX','guid':'a'*32,'messages':2},
            {'mailbox':'INBOX.Sent','guid':'b'*32,'messages':1},
            {'mailbox':'INBOX.Trash','guid':'c'*32,'messages':1},
            {'mailbox':'INBOX.Proyectos.Ñ','guid':'d'*32,'messages':1}]
        return {}
    def clear_disk_cache(self):return {'status':'cleared','retry_after_seconds':300}


class MailTests(unittest.TestCase):
    def setUp(self):
        root=ROOT/'artifacts';root.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(prefix='cct-mail-test-',dir=root)
        Path(self.temp.name).resolve().relative_to(ROOT.resolve())
        self.addCleanup(self.temp.cleanup)
        self.home=Path(self.temp.name);self.owner=core.Account('owner',self.home,self.home.stat().st_uid)
        self.api=API();self.cutoff={'date':'2025-09-19','utc_offset_minutes':-180}
        self.epoch=mail.normalize_cutoff(self.cutoff)['epoch']
    def item(self):
        p=self.home/'message';p.write_bytes(b'x'*4096);os.utime(p,(self.epoch-86400,self.epoch-86400))
        return core.make_item(self.owner,'mail/example.com/ventas/cur/message',p.stat(),'mail_date','review','old',
             account='ventas@example.com',mailbox='INBOX',mailbox_guid='a'*32,uid=123,uid_validity='42'),p.stat()
    def scanner(self,items):return lambda *args,**kwargs:SimpleNamespace(scan=lambda:{'items':items,'warnings':[]})
    def test_cutoff_is_exclusive_midnight_in_chosen_offset(self):
        result=mail.normalize_cutoff(self.cutoff)
        expected=int(datetime(2025,9,19,3,tzinfo=timezone.utc).timestamp())
        self.assertEqual(result['epoch'],expected)
        self.assertEqual(mail.normalize_cutoff({'date':'2025-09-19','utc_offset_minutes':330})['epoch'],expected-510*60)
    def test_invalid_future_and_injected_dates_rejected(self):
        for value in ({},None,{'date':'2025-02-30','utc_offset_minutes':0},
            {'date':'2100-01-01','utc_offset_minutes':0},{'date':'2025-09-19 OR ALL','utc_offset_minutes':0},
            {'date':'2025-09-19','utc_offset_minutes':True},{'date':'2025-09-19','utc_offset_minutes':900},
            {'date':'2025-09-19','utc_offset_minutes':0,'start_hour':24},
            {'date':'2025-09-19','utc_offset_minutes':0,'start_minute':True}):
            with self.subTest(value=value),self.assertRaises(core.CleanupError):mail.normalize_cutoff(value)
    def test_cutoff_keeps_normalized_day_start_if_midnight_is_skipped(self):
        value=mail.normalize_cutoff({'date':'2026-09-06','utc_offset_minutes':-180,'start_hour':1,'start_minute':0})
        self.assertEqual(value['epoch'],int(datetime(2026,9,6,4,tzinfo=timezone.utc).timestamp()))
        self.assertEqual(value['start_hour'],1)
    def test_only_accounts_owned_by_current_cpanel_user_are_available(self):
        addresses={a['address'] for a in mail.list_mail_accounts(self.owner,self.api)}
        self.assertEqual(addresses,{'owner','ventas@example.com','contacto@example.com'})
        with self.assertRaises(core.CleanupError):mail.checked_accounts(self.owner,self.api,['other@external.com'])
    def test_duplicate_and_empty_account_selections_rejected(self):
        for addresses in ([],['owner','owner'],['owner',None],['owner']*101):
            with self.assertRaises(core.CleanupError):mail.checked_accounts(self.owner,self.api,addresses)
    def test_confirmation_and_scope_ack_required_before_mutation(self):
        item,_=self.item()
        for word,ack,scope,all_ack in [('ELIMINAR',True,'spam_trash',False),('ELIMINAR CORREOS',False,'spam_trash',False),('ELIMINAR CORREOS',True,'all',False)]:
            with self.assertRaises(core.CleanupError):mail.delete_mail_by_date(self.owner,self.api,[item],self.cutoff,scope,word,ack,all_ack)
        self.assertFalse(self.api.calls)
    def test_invalid_scope_rejected(self):
        with self.assertRaises(core.CleanupError):mail.checked_scope('INBOX OR ALL')
    def test_delete_uses_only_exact_uids_and_confirmed_before_boundary(self):
        item,st=self.item()
        with patch.object(core,'safe_stat',side_effect=[st,FileNotFoundError()]),patch.object(core,'unlink_candidate') as unlink:
            result=mail.delete_mail_by_date(self.owner,self.api,[item],self.cutoff,'all','ELIMINAR CORREOS',True,True,scanner_factory=self.scanner([item]))
        unlink.assert_not_called()
        self.assertEqual(result['deleted_ids'],[item['id']])
        calls=[params for module,function,params in self.api.calls if function=='expunge_messages_for_mailbox_guid']
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0]['query'],f'UID 123 BEFORE {self.epoch:010d}')
        self.assertNotIn('ALL',calls[0]['query'])
    def test_forged_other_account_cannot_reach_mailbox_delete(self):
        item,_=self.item();item['account']='other@external.com'
        with self.assertRaises(core.CleanupError):mail.delete_mail_by_date(self.owner,self.api,[item],self.cutoff,'all','ELIMINAR CORREOS',True,True)
        self.assertFalse(any(function=='expunge_messages_for_mailbox_guid' for _,function,_ in self.api.calls))
    def test_message_at_cutoff_or_newer_is_never_deleted(self):
        for age in (0,1):
            item,_=self.item();item['modified_at']=self.epoch+age
            result=mail.delete_mail_by_date(self.owner,self.api,[item],self.cutoff,'all','ELIMINAR CORREOS',True,True,scanner_factory=self.scanner([item]))
            self.assertEqual(result['deleted_ids'],[]);self.assertEqual(len(result['failed']),1)
        self.assertFalse(any(function=='expunge_messages_for_mailbox_guid' for _,function,_ in self.api.calls))
    def test_stale_uid_validity_or_fingerprint_requires_new_preview(self):
        current,_=self.item()
        for key,value in [('uid_validity','99'),('fingerprint',[0,1,2])]:
            requested=copy.deepcopy(current);requested[key]=value
            result=mail.delete_mail_by_date(self.owner,self.api,[requested],self.cutoff,'all','ELIMINAR CORREOS',True,True,scanner_factory=self.scanner([current]))
            self.assertEqual(result['deleted_ids'],[])
        self.assertFalse(any(function=='expunge_messages_for_mailbox_guid' for _,function,_ in self.api.calls))
    def test_maildir_timestamp_filter_excludes_exact_boundary(self):
        scanner=core.Scanner(self.owner,self.api);scanner.mail_cutoff=self.epoch;scanner.mailbox_name='INBOX'
        scanner._mail_names={('mail/.Trash','cur'):{'message':'message'},('mail/.Trash','new'):{}}
        p=self.home/'message';p.write_bytes(b'old');
        # Exercise the actual shared Maildir filter without POSIX descriptor IO.
        for when,expected in ((self.epoch-1,1),(self.epoch,0),(self.epoch+1,0)):
            os.utime(p,(when,when));scanner.items=[]
            with patch.object(core.os,'O_DIRECTORY',0,create=True),patch.object(core.os,'O_NOFOLLOW',0,create=True),patch.object(core.os,'open',return_value=99),patch.object(core.os,'close'),patch.object(core.os,'stat',return_value=p.stat()):
                scanner._mail_message('owner','mail/.Trash',1,'message',1,'a'*32,'42','mail_date')
            self.assertEqual(len(scanner.items),expected)
    def test_folder_scope_and_custom_names_are_resolved_by_guid(self):
        for scope,expected in [('spam_trash',['INBOX.Trash']),('all',['INBOX','INBOX.Sent','INBOX.Trash','INBOX.Proyectos.Ñ'])]:
            scanner=mail.MailDateScanner(self.owner,self.api,['ventas@example.com'],mail.normalize_cutoff(self.cutoff),scope)
            scanner._paths_by_guid=lambda _: {letter*32:f'mail/physical-{letter}' for letter in 'abcd'}
            seen=[]
            def inspect(*args):seen.append(scanner.mailbox_name)
            scanner.maildir=inspect
            result=scanner.scan()
            self.assertEqual(seen,expected);self.assertFalse(result['partial'])
    def test_missing_maildir_index_is_explicitly_partial_not_empty_success(self):
        scanner=mail.MailDateScanner(self.owner,self.api,['ventas@example.com'],mail.normalize_cutoff(self.cutoff),'all')
        scanner._paths_by_guid=lambda _: {}
        result=scanner.scan()
        self.assertTrue(result['partial']);self.assertTrue(result['warnings']);self.assertEqual(result['messages'],0)
    def test_preview_rechecks_account_membership_and_uses_same_cutoff(self):
        seen=[]
        def factory(owner,api,addresses,cutoff,scope):
            seen.append((addresses,cutoff,scope));return SimpleNamespace(scan=lambda:{'items':[]})
        result=mail.preview_mail_by_date(self.owner,self.api,['owner'],self.cutoff,'spam_trash',scanner_factory=factory)
        self.assertEqual(seen[0][1]['epoch'],self.epoch);self.assertIn('quota',result)


if __name__=='__main__':unittest.main()
