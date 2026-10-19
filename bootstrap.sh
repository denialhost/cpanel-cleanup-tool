#!/bin/bash
# Installer for the public, versioned GitHub release. Also used for updates.
set -euo pipefail

VERSION="${1:-${CCT_VERSION:-0.2.1}}"
VERSION="${VERSION#v}"
if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ || $# -gt 1 ]]; then
    echo 'Uso: bash install-cct.sh [0.2.1]' >&2
    exit 1
fi
if [[ "$(id -u)" != 0 ]]; then
    echo 'Ejecuta el instalador como root en el servidor cPanel.' >&2
    exit 1
fi
if [[ ! -x /usr/local/cpanel/scripts/install_plugin || ! -d /usr/local/cpanel/base/frontend/jupiter ]]; then
    echo 'Se requiere cPanel & WHM con el tema Jupiter.' >&2
    exit 1
fi
PYTHON="${CCT_PYTHON:-/usr/bin/python3}"
if [[ "$PYTHON" != /* || "$PYTHON" == *[[:space:]]* || ! -x "$PYTHON" ]]; then
    echo 'Define CCT_PYTHON como la ruta absoluta de Python 3.9+, sin espacios.' >&2
    exit 1
fi
"$PYTHON" -I -c 'import sys; assert sys.version_info >= (3,9), "Se requiere Python 3.9 o superior"'
command -v curl >/dev/null || { echo 'Se requiere curl.' >&2; exit 1; }
WORK="$(mktemp -d /tmp/cct-install.XXXXXXXX)"
cleanup() {
    if [[ "$WORK" == /tmp/cct-install.* && -d "$WORK" && ! -L "$WORK" ]]; then
        rm -rf -- "$WORK"
    fi
}
trap cleanup EXIT
BASE="https://github.com/denialhost/cpanel-cleanup-tool/releases/download/v${VERSION}"
for filename in "cct-${VERSION}.tar.gz" SHA256SUMS; do
    curl --proto '=https' --tlsv1.2 --fail --show-error --silent --location \
        --retry 3 --connect-timeout 15 --max-time 180 \
        "$BASE/$filename" -o "$WORK/$filename"
done
"$PYTHON" -I - "$WORK" "$VERSION" <<'PY'
import hashlib, hmac, pathlib, re, shutil, sys, tarfile
work, version = pathlib.Path(sys.argv[1]), sys.argv[2]
name = 'cct-' + version + '.tar.gz'
matches = [line.split() for line in (work / 'SHA256SUMS').read_text(encoding='ascii').splitlines()
           if len(line.split()) == 2 and line.split()[1].lstrip('*') == name]
if len(matches) != 1 or not re.fullmatch(r'[0-9a-f]{64}', matches[0][0]):
    raise SystemExit('No se encontró una suma SHA-256 válida para el paquete.')
if not hmac.compare_digest(hashlib.sha256((work / name).read_bytes()).hexdigest(), matches[0][0]):
    raise SystemExit('La verificación SHA-256 falló. No se instalará el paquete.')
destination = work / 'source'
destination.mkdir(mode=0o700)
with tarfile.open(work / name, 'r:gz') as archive:
    members = archive.getmembers()
    if len(members) > 500 or sum(member.size for member in members) > 64 * 1024 * 1024:
        raise SystemExit('El paquete excede los límites de instalación.')
    for member in members:
        parts = pathlib.PurePosixPath(member.name).parts
        if not parts or parts[0] != 'cct' or '..' in parts or not member.isfile():
            raise SystemExit('El paquete contiene una entrada no permitida.')
        target = destination.joinpath(*parts)
        target.resolve().relative_to(destination.resolve())
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.extractfile(member) as source, target.open('xb') as output:
            shutil.copyfileobj(source, output)
        target.chmod(0o644)
if (destination / 'cct/VERSION').read_text().strip() != version:
    raise SystemExit('La versión del paquete no coincide con la solicitada.')
print('Paquete v' + version + ' verificado.')
PY
bash "$WORK/source/cct/install.sh"
