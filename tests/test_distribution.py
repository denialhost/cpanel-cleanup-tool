"""Exercise the published package and the standalone installer's verifier."""
import hashlib
import io
import json
import re
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DistributionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.version = (ROOT / 'VERSION').read_text().strip()
        cls.archive = ROOT / 'artifacts' / ('cct-' + cls.version + '.tar.gz')
        subprocess.run([sys.executable, str(ROOT / 'tools/package.py')], check=True, capture_output=True)
        cls.verifier = (ROOT / 'bootstrap.sh').read_text().split("<<'PY'\n", 1)[1].split('\nPY\n', 1)[0]

    def test_published_assets_match_checksums_and_version(self):
        for line in (ROOT / 'artifacts/SHA256SUMS').read_text().splitlines():
            checksum, name = line.split()
            self.assertEqual(hashlib.sha256((ROOT / 'artifacts' / name).read_bytes()).hexdigest(), checksum)
        self.assertEqual((ROOT / 'artifacts/install-cct.sh').read_bytes(), (ROOT / 'bootstrap.sh').read_bytes().replace(b'\r\n', b'\n'))
        with tarfile.open(self.archive) as archive:
            self.assertEqual(archive.extractfile('cct/VERSION').read().decode().strip(), self.version)
            self.assertEqual(archive.getmember('cct/install.sh').mode, 0o755)
            self.assertEqual(archive.getmember('cct/plugin/index.live.cgi').mode, 0o755)
            self.assertEqual(archive.getmember('cct/plugin/index.live.pl').mode, 0o755)
            self.assertIn(b'Cpanel::LiveAPI->new()', archive.extractfile('cct/plugin/index.live.pl').read())
            for image in (ROOT / 'docs/screenshots').iterdir():
                if image.suffix.lower() in {'.jpg', '.png'}:
                    self.assertEqual(archive.extractfile('cct/docs/screenshots/' + image.name).read(), image.read_bytes())
            for resource in set(re.findall(r'\]\((docs/screenshots/[^)]+)\)', (ROOT / 'README.md').read_text(encoding='utf-8'))):
                self.assertTrue(archive.getmember('cct/' + resource).isfile())
            self.assertTrue(all(member.isfile() and member.name.startswith('cct/') and '..' not in Path(member.name).parts for member in archive))

    def test_package_is_reproducible(self):
        before = self.archive.read_bytes()
        subprocess.run([sys.executable, str(ROOT / 'tools/package.py')], check=True, capture_output=True)
        self.assertEqual(self.archive.read_bytes(), before)

    def test_native_shell_and_embedded_app_are_installed_together(self):
        with tarfile.open(self.archive) as archive:
            template = archive.extractfile('cct/plugin/home.html.tt').read().decode()
            self.assertIn("WRAPPER '_assets/master.html.tt'", template)
            self.assertIn("CPANEL.CPVAR.dprefix", template)
            self.assertIn("CPANEL.feature('cct')", template)
            self.assertIn('src="index.live.pl?embedded=1"', template)
            self.assertIn("frame-ancestors 'self'", archive.extractfile('cct/plugin/index.live.pl').read().decode())
            self.assertEqual(json.loads(archive.extractfile('cct/install.json').read())[0]['uri'], 'cct/home.html')
            for script in ('install.sh', 'uninstall.sh'):
                text = archive.extractfile('cct/' + script).read().decode()
                for resource in ('home.html.tt', 'shell.css'):
                    self.assertIn(resource, text)
            self.assertTrue(archive.getmember('cct/plugin/shell.css').isfile())

    def test_browser_module_graph_is_packaged_with_cpanel_compatible_extensions(self):
        with tarfile.open(self.archive) as archive:
            source = archive.extractfile('cct/plugin/index.html').read().decode()
            entry = re.search(r'<script type="module" src="([^"]+)"', source).group(1)
            self.assertIn('?v=' + self.version, entry)
            self.assertIn('styles.css?v=' + self.version, source)
            pending, seen = [entry], set()
            while pending:
                resource = pending.pop()
                self.assertIn('?v=' + self.version, resource)
                name = resource.split('?', 1)[0]
                if name in seen:
                    continue
                self.assertTrue(name.endswith('.js'), 'cpsrvd must serve each module with a JavaScript MIME type')
                self.assertNotIn('/', name)
                seen.add(name)
                module = archive.extractfile('cct/plugin/' + name).read().decode()
                pending.extend(re.findall(r"from ['\"]\./([^'\"]+)['\"]", module))
            self.assertGreaterEqual(len(seen), 4)
            self.assertEqual(json.loads(archive.extractfile('cct/package.json').read())['type'], 'module')

    def verify(self, entries=None, corrupt=False):
        with tempfile.TemporaryDirectory(prefix='cct-release-test-', dir=ROOT / 'artifacts') as directory:
            work = Path(directory)
            destination = work / self.archive.name
            if entries is None:
                destination.write_bytes(self.archive.read_bytes())
            else:
                with tarfile.open(destination, 'w:gz') as archive:
                    for name, kind in entries:
                        member = tarfile.TarInfo(name)
                        member.type = kind
                        member.linkname = '/etc/passwd' if kind == tarfile.SYMTYPE else ''
                        member.size = 0
                        archive.addfile(member, io.BytesIO())
            checksum = '0' * 64 if corrupt else hashlib.sha256(destination.read_bytes()).hexdigest()
            (work / 'SHA256SUMS').write_text(checksum + '  ' + destination.name + '\n')
            result = subprocess.run([sys.executable, '-I', '-', str(work), self.version], input=self.verifier, text=True, capture_output=True)
            files = [str(path.relative_to(work)) for path in work.rglob('*') if path.is_file()]
            return result, files

    def test_verified_release_extracts_complete_installer(self):
        result, files = self.verify()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('source/cct/install.sh', [name.replace('\\', '/') for name in files])

    def test_corrupt_download_is_rejected_before_extraction(self):
        result, files = self.verify(corrupt=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(name.startswith('source') for name in files))

    def test_traversal_and_links_are_rejected_even_with_matching_checksum(self):
        for name, kind in [('cct/../../escape', tarfile.REGTYPE), ('/tmp/escape', tarfile.REGTYPE), ('cct/link', tarfile.SYMTYPE)]:
            with self.subTest(name=name):
                result, files = self.verify([(name, kind)])
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any('escape' in filename or filename.endswith('link') for filename in files))


if __name__ == '__main__':
    unittest.main()
