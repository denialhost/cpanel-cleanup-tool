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
if [[ ! -d /usr/local/cpanel/whostmgr/docroot/cgi || ! -x /usr/local/cpanel/bin/register_appconfig ]]; then
    echo 'No se encontró el entorno WHM AppConfig requerido por CCT.' >&2
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
WHM_TARGET=/usr/local/cpanel/whostmgr/docroot/cgi/cct
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
WHM_STAGE="$(mktemp -d /usr/local/cpanel/whostmgr/docroot/cgi/.cct-whm-stage.XXXXXXXX)"
PREVIOUS=''
WHM_PREVIOUS=''
COMMITTED=0
cleanup() {
    if [[ "$COMMITTED" == 0 && -n "$PREVIOUS" && -d "$PREVIOUS" ]]; then
        if [[ -d "$TARGET" && ! -L "$TARGET" ]]; then
            rm -rf -- "$TARGET"
        fi
        mv -T -- "$PREVIOUS" "$TARGET"
        PREVIOUS=''
        echo 'Se restauró la versión anterior del plugin cPanel.' >&2
    fi
    if [[ "$COMMITTED" == 0 && -n "$WHM_PREVIOUS" && -d "$WHM_PREVIOUS" ]]; then
        if [[ -d "$WHM_TARGET" && ! -L "$WHM_TARGET" ]]; then
            rm -rf -- "$WHM_TARGET"
        fi
        mv -T -- "$WHM_PREVIOUS" "$WHM_TARGET"
        WHM_PREVIOUS=''
        echo 'Se restauró la versión anterior del addon WHM.' >&2
    fi
    for directory in "$STAGE" "$PREVIOUS" "$WHM_STAGE" "$WHM_PREVIOUS"; do
        if [[ ( "$directory" == /usr/local/cpanel/base/frontend/jupiter/.cct-* || "$directory" == /usr/local/cpanel/whostmgr/docroot/cgi/.cct-* ) && -d "$directory" && ! -L "$directory" ]]; then
            rm -rf -- "$directory"
        fi
    done
}
trap cleanup EXIT
chmod 0755 "$STAGE" "$WHM_STAGE"
for filename in cct_core.py cct_mail.py cct_batch.py cct_backend.py cct_live.py cct_audit.py home.html.tt shell.css index.html styles.css app.js model.js mail.js mail-model.js activity.js icon.svg; do
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

# WHM root addon files (UI + CGI). Python engine remains in the Jupiter plugin dir.
install -m 0755 -o root -g root "$SOURCE/whm/addon_cct.cgi" "$WHM_STAGE/addon_cct.cgi"
for filename in index.html app.js styles.css cct.conf cct.tmpl; do
    install -m 0644 -o root -g root "$SOURCE/whm/$filename" "$WHM_STAGE/$filename"
done
# Shared UI modules (same files as the cPanel theme plugin).
for filename in model.js activity.js; do
    install -m 0644 -o root -g root "$SOURCE/plugin/$filename" "$WHM_STAGE/$filename"
done
# WHM chrome template (sidebar/header) lives with the other whostmgr templates.
install -m 0644 -o root -g root "$SOURCE/whm/cct.tmpl" /usr/local/cpanel/whostmgr/docroot/templates/cct.tmpl
/usr/local/cpanel/3rdparty/bin/perl -c "$WHM_STAGE/addon_cct.cgi"

if [[ -d "$TARGET" ]]; then
    PREVIOUS="$(mktemp -d /usr/local/cpanel/base/frontend/jupiter/.cct-previous.XXXXXXXX)"
    rmdir -- "$PREVIOUS"
    mv -T -- "$TARGET" "$PREVIOUS"
fi
if [[ -d "$WHM_TARGET" ]]; then
    WHM_PREVIOUS="$(mktemp -d /usr/local/cpanel/whostmgr/docroot/cgi/.cct-whm-previous.XXXXXXXX)"
    rmdir -- "$WHM_PREVIOUS"
    mv -T -- "$WHM_TARGET" "$WHM_PREVIOUS"
fi
mv -T -- "$STAGE" "$TARGET"
mv -T -- "$WHM_STAGE" "$WHM_TARGET"
# Expose the CGI at the AppConfig entry URL.
install -m 0755 -o root -g root "$WHM_TARGET/addon_cct.cgi" /usr/local/cpanel/whostmgr/docroot/cgi/addon_cct.cgi
/usr/local/cpanel/scripts/install_plugin "$SOURCE" --theme=jupiter
if ! /usr/local/cpanel/bin/register_appconfig "$WHM_TARGET/cct.conf"; then
    echo 'No se pudo registrar el addon WHM en AppConfig.' >&2
    exit 1
fi
if [[ ! -f /var/cpanel/apps/cct.conf ]]; then
    echo 'AppConfig no dejó /var/cpanel/apps/cct.conf; el icono WHM no aparecerá.' >&2
    exit 1
fi
COMMITTED=1
printf 'CCT v%s instalado.\n' "$(cat "$TARGET/VERSION")"
printf '%s\n' 'cPanel: Feature Manager → "Limpieza de espacio Plugin" (cct).'
printf '%s\n' 'WHM root: busca "CCT Limpieza de espacio" (Plugins) o en el buscador de WHM.'
