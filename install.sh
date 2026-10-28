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
# Probe only fixed system locations; an explicit CCT_PYTHON must never be ignored.
select_python() {
    local candidate
    if [[ ${CCT_PYTHON+x} ]]; then
        set -- "$CCT_PYTHON"
    fi
    for candidate in "$@"; do
        if [[ "$candidate" =~ ^/[a-zA-Z0-9_./+-]+$ && -x "$candidate" ]] &&
            "$candidate" -I -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)' >/dev/null 2>&1; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    echo 'No se encontró Python 3.9 o superior utilizable por CCT.' >&2
    if [[ ${CCT_PYTHON+x} ]]; then
        echo 'La ruta indicada en CCT_PYTHON no es válida o su versión es incompatible.' >&2
    fi
    echo 'Si ya está instalado, indica su ruta: CCT_PYTHON=/usr/bin/python3.12 bash install-cct.sh [versión].' >&2
    echo 'El instalador no modifica el Python predeterminado ni instala paquetes del sistema.' >&2
    return 1
}
PYTHON="$(select_python /usr/bin/python3 /usr/bin/python3.[0-9][0-9] /usr/bin/python3.[0-9] \
    /usr/local/cpanel/3rdparty/bin/python3 /usr/local/bin/python3 \
    /usr/local/bin/python3.[0-9][0-9] /usr/local/bin/python3.[0-9])"
# Pass the same runtime to the downloaded installer, including older releases.
export CCT_PYTHON="$PYTHON"
printf 'Python seleccionado: %s\n' "$PYTHON"
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
for filename in cct_core.py cct_mail.py cct_backend.py cct_live.py home.html.tt shell.css index.html styles.css app.js model.js mail.js mail-model.js activity.js icon.svg; do
    install -m 0644 -o root -g root "$SOURCE/plugin/$filename" "$STAGE/$filename"
done
# Jupiter resolves page_stylesheets to their .min.css variant in production.
install -m 0644 -o root -g root "$SOURCE/plugin/shell.css" "$STAGE/shell.min.css"
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
