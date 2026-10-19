"""Create a deterministic source/install tarball with Unix permissions/newlines."""
import gzip
import hashlib
import io
import re
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts"
OUT.mkdir(exist_ok=True)
version = (ROOT / "VERSION").read_text(encoding="ascii").strip()
if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
    raise SystemExit("Invalid VERSION")
target = OUT / ("cct-" + version + ".tar.gz")
files = [ROOT / n for n in ("README.md", "CHANGELOG.md", "VERSION", "preview.py", "install.json", "uninstall.json", "install.sh", "uninstall.sh", "bootstrap.sh", "tools/package.py")]
files += sorted(p for p in (ROOT / "tests").iterdir() if p.is_file())
files += sorted(p for p in (ROOT / "plugin").iterdir() if p.is_file() and p.suffix != ".pyc")
with target.open("wb") as output, gzip.GzipFile(filename="", fileobj=output, mode="wb", mtime=0) as compressed, tarfile.open(fileobj=compressed, mode="w") as archive:
    for source in files:
        data = source.read_bytes().replace(b"\r\n", b"\n")
        entry = tarfile.TarInfo("cct/" + source.relative_to(ROOT).as_posix())
        entry.size = len(data)
        entry.mode = 0o755 if source.name in {"install.sh", "uninstall.sh", "bootstrap.sh", "index.live.cgi"} else 0o644
        entry.mtime = 0
        archive.addfile(entry, io.BytesIO(data))
    data = (ROOT / "plugin/icon.svg").read_bytes()
    entry = tarfile.TarInfo("cct/icon.svg")
    entry.size, entry.mode, entry.mtime = len(data), 0o644, 0
    archive.addfile(entry, io.BytesIO(data))
(OUT / "install-cct.sh").write_bytes((ROOT / "bootstrap.sh").read_bytes().replace(b"\r\n", b"\n"))
(OUT / "VERSION").write_bytes((version + "\n").encode("ascii"))
assets = [target, OUT / "install-cct.sh", OUT / "VERSION"]
checksums = "".join(hashlib.sha256(asset.read_bytes()).hexdigest() + "  " + asset.name + "\n" for asset in assets)
(OUT / "SHA256SUMS").write_bytes(checksums.encode("ascii"))
print(target)
print(checksums, end="")
