#!/bin/bash
set -euo pipefail

if [[ "$(id -u)" != 0 ]]; then
    echo 'La instalación debe ejecutarse como root en el servidor cPanel.' >&2
    exit 1
fi
if [[ ! -x /usr/local/cpanel/scripts/install_plugin || ! -d /usr/local/cpanel/base/frontend/jupiter ]]; then
    echo 'Se requiere cPanel & WHM con el tema Jupiter.' >&2
    exit 1
fi
if [[ ! -x /usr/local/cpanel/3rdparty/bin/perl || ! -f /usr/local/cpanel/Cpanel/LiveAPI.pm ]]; then
    echo 'No se encontró el entorno LiveAPI de Perl incluido con cPanel.' >&2
    exit 1
fi
PYTHON="${CCT_PYTHON:-/usr/bin/python3}"
if [[ ! "$PYTHON" =~ ^/[a-zA-Z0-9_./+-]+$ || ! -x "$PYTHON" ]]; then
    echo 'Define CCT_PYTHON como la ruta absoluta de Python 3.9 o superior, sin espacios.' >&2
    exit 1
fi
"$PYTHON" -I -c 'import sys; assert sys.version_info >= (3,9), "Se requiere Python 3.9 o superior"'
SOURCE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
TARGET=/usr/local/cpanel/base/frontend/jupiter/cct
if [[ -L "$TARGET" || ( -e "$TARGET" && ! -d "$TARGET" ) ]]; then
    echo 'El destino debe ser una carpeta real, sin enlaces simbólicos.' >&2
    exit 1
fi
if [[ -d "$TARGET" && ! -f "$TARGET/install.json" ]]; then
    echo 'La carpeta de destino existe pero no corresponde a una instalación de CCT.' >&2
    exit 1
fi
# Prepare all files before replacing the installed version. Both paths are
# created under the fixed cPanel frontend directory, on the same filesystem.
STAGE="$(mktemp -d /usr/local/cpanel/base/frontend/jupiter/.cct-stage.XXXXXXXX)"
PREVIOUS=''
COMMITTED=0
cleanup() {
    if [[ "$COMMITTED" == 0 && -n "$PREVIOUS" && -d "$PREVIOUS" ]]; then
        if [[ -d "$TARGET" && ! -L "$TARGET" ]]; then
            rm -rf -- "$TARGET"
        fi
        mv -T -- "$PREVIOUS" "$TARGET"
        PREVIOUS=''
        echo 'Se restauró la versión anterior del plugin.' >&2
    fi
    for directory in "$STAGE" "$PREVIOUS"; do
        if [[ "$directory" == /usr/local/cpanel/base/frontend/jupiter/.cct-* && -d "$directory" && ! -L "$directory" ]]; then
            rm -rf -- "$directory"
        fi
    done
}
trap cleanup EXIT
chmod 0755 "$STAGE"
for filename in cct_core.py cct_mail.py cct_backend.py cct_live.py index.html styles.css app.js model.js mail.js mail-model.js icon.svg; do
    install -m 0644 -o root -g root "$SOURCE/plugin/$filename" "$STAGE/$filename"
done
for filename in index.live.pl index.live.cgi; do
    install -m 0755 -o root -g root "$SOURCE/plugin/$filename" "$STAGE/$filename"
done
printf '%s\n' "$PYTHON" > "$STAGE/python-path"
chmod 0644 "$STAGE/python-path"
/usr/local/cpanel/3rdparty/bin/perl -c "$STAGE/index.live.pl"
"$PYTHON" -I -c 'import ast, pathlib, sys; [ast.parse(path.read_text(encoding="utf-8")) for path in pathlib.Path(sys.argv[1]).glob("*.py")]' "$STAGE"
for filename in install.json uninstall.json VERSION; do
    install -m 0644 -o root -g root "$SOURCE/$filename" "$STAGE/$filename"
done
install -m 0755 -o root -g root "$SOURCE/uninstall.sh" "$STAGE/uninstall.sh"
install -m 0644 -o root -g root "$SOURCE/plugin/icon.svg" "$SOURCE/icon.svg"
if [[ -d "$TARGET" ]]; then
    PREVIOUS="$(mktemp -d /usr/local/cpanel/base/frontend/jupiter/.cct-previous.XXXXXXXX)"
    rmdir -- "$PREVIOUS"
    mv -T -- "$TARGET" "$PREVIOUS"
fi
mv -T -- "$STAGE" "$TARGET"
/usr/local/cpanel/scripts/install_plugin "$SOURCE" --theme=jupiter
COMMITTED=1
printf 'CCT v%s instalado.\n' "$(cat "$TARGET/VERSION")"
printf '%s\n' 'CCT instalado. En Feature Manager busca "Limpieza de espacio Plugin" (cct).'
printf '%s\n' 'Abre una cuenta de prueba en cPanel → Archivos → Limpieza de espacio.'
