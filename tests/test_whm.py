import importlib.machinery
import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugin'))
import cct_core as core


def load_backend():
    loader = importlib.machinery.SourceFileLoader('cct_entry_whm', str(ROOT / 'plugin' / 'cct_backend.py'))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class WhmBackendTests(unittest.TestCase):
    def setUp(self):
        self.backend = load_backend()

    def test_check_post_accepts_whm_https_port(self):
        env = {
            'REQUEST_METHOD': 'POST',
            'CONTENT_TYPE': 'application/json',
            'HTTP_X_CCT_REQUEST': '1',
            'HTTP_HOST': 'server:2087',
            'SERVER_PORT': '2087',
            'HTTP_ORIGIN': 'https://server:2087',
        }
        self.backend.check_post(env)

    def test_accounts_action_rejects_non_root(self):
        transport = Mock()
        with patch.dict(os.environ, {'CCT_VIA': 'whm', 'REQUEST_METHOD': 'GET', 'QUERY_STRING': 'action=accounts'}, clear=False):
            with patch.object(os, 'geteuid', return_value=1000, create=True):
                response = self.backend.main(transport=transport, body_bytes=b'')
        body = __import__('json').loads(response['body'])
        self.assertIn('error', body)
        self.assertIn('root', body['error'].lower())

    def test_list_whm_accounts_parses_whmapi_document(self):
        transport = Mock()
        transport.call.return_value = {
            'data': {
                'acct': [
                    {'user': 'alice', 'domain': 'a.example', 'plan': 'basic', 'diskused': '1M', 'disklimit': '10M'},
                    {'user': 'bad user', 'domain': 'x'},
                    {'username': 'bob', 'domain': 'b.example', 'package': 'pro'},
                ]
            }
        }
        rows = core.list_whm_accounts(core.UAPI(None, transport=transport))
        self.assertEqual([row['user'] for row in rows], ['alice', 'bob'])
        self.assertEqual(rows[0]['domain'], 'a.example')
        transport.call.assert_called_once_with('whm', 'listaccts', 'list', {})

    @unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid'), 'Account impersonation is POSIX-only')
    def test_from_username_rejects_non_root(self):
        if os.geteuid() == 0:
            self.skipTest('Running as root')
        with self.assertRaises(core.CleanupError):
            core.Account.from_username('nobody')

    def test_addon_cgi_guards_root_session(self):
        source = (ROOT / 'whm' / 'addon_cct.cgi').read_text(encoding='utf-8')
        self.assertIn("REMOTE_USER", source)
        self.assertIn("$< == 0", source)
        self.assertIn("CCT_TARGET_USER", source)
        self.assertIn("CCT_VIA", source)
        self.assertIn("'whm/listaccts/list'", source)
        self.assertIn('uapi/Quota/get_quota_info', source)

    def test_whm_appconfig_has_menu_entry(self):
        conf = (ROOT / 'whm' / 'cct.conf').read_text(encoding='utf-8')
        self.assertRegex(conf, r'(?m)^name=cct$')
        self.assertRegex(conf, r'(?m)^service=whostmgr$')
        self.assertRegex(conf, r'(?m)^url=/cgi/addon_cct\.cgi$')
        self.assertRegex(conf, r'(?m)^entryurl=addon_cct\.cgi$')
        self.assertRegex(conf, r'(?m)^displayname=CCT Limpieza de espacio$')
        self.assertRegex(conf, r'(?m)^acls=all$')
        self.assertRegex(conf, r'(?m)^target=_self$')

    def test_whm_ui_keeps_cpsession_relative_assets(self):
        html = (ROOT / 'whm' / 'index.html').read_text(encoding='utf-8')
        app = (ROOT / 'whm' / 'app.js').read_text(encoding='utf-8')
        self.assertNotIn('/cgi/addon_cct.cgi?action=asset', html)
        self.assertIn('href="?action=asset&file=styles.css', html)
        self.assertIn('src="?action=asset&file=app.js', html)
        # Inline import maps are blocked by CSP; WHM ships a single bundled module.
        self.assertNotIn('importmap', html)
        self.assertNotIn('<script>window.CCT_API', html)
        self.assertIn('CCT WHM bundle', app)
        self.assertNotIn('import(', app)
        self.assertTrue((ROOT / 'whm' / 'app.ui.js').is_file())
        self.assertIn('id="account-sort"', html)
        self.assertNotIn('id="usage-chart"', html)
        self.assertIn('site-footer', html)
        self.assertIn('DenialHost', html)
        ui = (ROOT / 'whm' / 'app.ui.js').read_text(encoding='utf-8')
        self.assertIn("sort: 'usage'", ui)
        self.assertIn('// «Revisar» abre la cuenta y lanza el análisis de inmediato.', ui)
        self.assertRegex(ui, r'function openAccount\(user\)[\s\S]*?\n  scan\(\);')

    def test_whm_wraps_spa_in_whostmgr_template(self):
        cgi = (ROOT / 'whm' / 'addon_cct.cgi').read_text(encoding='utf-8')
        tmpl = (ROOT / 'whm' / 'cct.tmpl').read_text(encoding='utf-8')
        self.assertIn("Cpanel::Template::process_template", cgi)
        self.assertIn("Content-type: text/html", cgi)
        self.assertIn("embedded", cgi)
        self.assertIn("master_templates/master.tmpl", tmpl)
        self.assertIn("iframe_src", tmpl)


if __name__ == '__main__':
    unittest.main()
