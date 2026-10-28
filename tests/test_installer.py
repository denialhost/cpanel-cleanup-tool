"""Exercise runtime selection before downloading or changing a cPanel install."""
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which('bash')
if not BASH and os.name == 'nt':
    candidate = Path(r'C:\Program Files\Git\bin\bash.exe')
    if candidate.is_file():
        BASH = str(candidate)


def shell_path(path):
    value = path.as_posix()
    if os.name == 'nt':
        value = '/' + value[0].lower() + value[2:]
    return value


@unittest.skipUnless(BASH, 'Bash is required for installer runtime checks')
class InstallerRuntimeTests(unittest.TestCase):
    def setUp(self):
        (ROOT / 'artifacts').mkdir(exist_ok=True)
        self.work = tempfile.TemporaryDirectory(prefix='cct-python-', dir=ROOT / 'artifacts')
        self.addCleanup(self.work.cleanup)
        self.folder = Path(self.work.name)
        self.old = self.runtime('python3', 1)
        self.modern = self.runtime('python3.12', 0)

    def runtime(self, name, status):
        path = self.folder / name
        path.write_text('#!/bin/bash\n[[ "$1" == -I && "$2" == -c ]] || exit 7\nexit ' + str(status) + '\n', encoding='utf-8')
        path.chmod(0o755)
        return shell_path(path)

    def selections(self, candidates, override=None):
        results = []
        for name in ('bootstrap.sh', 'install.sh'):
            source = (ROOT / name).read_text(encoding='utf-8')
            # Run the real preflight, replacing only its fixed OS locations.
            start = source.index('select_python() {')
            end = source.index("printf 'Python seleccionado: %s\\n' \"$PYTHON\"")
            block = source[start:end]
            block = re.sub(r'PYTHON="\$\(select_python .*?\)"',
                           lambda _: 'PYTHON="$(select_python ' + ' '.join(map(shlex.quote, candidates)) + ')"',
                           block, count=1, flags=re.S)
            environment = dict(os.environ)
            environment.pop('CCT_PYTHON', None)
            if override is not None:
                environment['CCT_PYTHON'] = override
            result = subprocess.run([BASH, '-c', 'set -euo pipefail\n' + block +
                                     '\n/bin/bash -c \'printf "%s" "$CCT_PYTHON"\''],
                                    env=environment, capture_output=True, text=True, encoding='utf-8', timeout=15)
            results.append(result)

        return results

    def test_old_default_falls_back_to_modern_python_and_exports_to_child(self):
        for result in self.selections([self.old, self.modern]):
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, self.modern)

    def test_compatible_default_is_kept(self):
        for result in self.selections([self.modern, self.old]):
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, self.modern)

    def test_explicit_runtime_takes_precedence(self):
        custom = self.runtime('python-custom', 0)
        for result in self.selections([self.modern], override=custom):
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, custom)

    def test_explicit_incompatible_runtime_does_not_silently_fall_back(self):
        for result in self.selections([self.modern], override=self.old):
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('CCT_PYTHON', result.stderr)
            self.assertNotIn('Traceback', result.stderr)
            self.assertEqual(result.stdout, '')

    def test_missing_compatible_runtime_has_a_readable_error(self):
        for result in self.selections([self.old, shell_path(self.folder / 'missing')]):
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('No se encontró Python 3.9', result.stderr)
            self.assertNotIn('Traceback', result.stderr)

    def test_invalid_explicit_paths_are_rejected(self):
        for invalid in ('python3.12', self.modern + ' with-space', ''):
            for result in self.selections([self.modern], override=invalid):
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('CCT_PYTHON', result.stderr)
                self.assertEqual(result.stdout, '')


if __name__ == '__main__':
    unittest.main()
