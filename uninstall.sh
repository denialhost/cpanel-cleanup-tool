#!/bin/bash
set -euo pipefail
if [[ "$(id -u)" != 0 ]]; then
    echo 'La desinstalación requiere root.' >&2
    exit 1
fi
TARGET=/usr/local/cpanel/base/frontend/jupiter/cct
WHM_TARGET=/usr/local/cpanel/whostmgr/docroot/cgi/cct
if [[ -L "$TARGET" || ! -f "$TARGET/install.json" ]]; then
    echo 'No se encontró una instalación válida de CCT.' >&2
    exit 1
fi
if [[ -f "$WHM_TARGET/cct.conf" && -x /usr/local/cpanel/bin/unregister_appconfig ]]; then
    /usr/local/cpanel/bin/unregister_appconfig "$WHM_TARGET/cct.conf" || true
fi
/usr/local/cpanel/scripts/uninstall_plugin "$TARGET" --theme=jupiter
# Only named plugin files; never recurse or touch any customer home directory.
for filename in index.live.pl index.live.cgi cct_core.py cct_mail.py cct_batch.py cct_backend.py cct_live.py cct_audit.py python-path home.html.tt shell.css shell.min.css index.html styles.css app.js model.js mail.js mail-model.js activity.js app.mjs model.mjs mail.mjs mail-model.mjs icon.svg install.json uninstall.json VERSION uninstall.sh; do
    rm -f -- "$TARGET/$filename"
done
rmdir -- "$TARGET" 2>/dev/null || true
rm -f -- /usr/local/cpanel/whostmgr/docroot/cgi/addon_cct.cgi
if [[ -d "$WHM_TARGET" && ! -L "$WHM_TARGET" ]]; then
    for filename in addon_cct.cgi index.html app.js styles.css cct.conf; do
        rm -f -- "$WHM_TARGET/$filename"
    done
    rmdir -- "$WHM_TARGET" 2>/dev/null || true
fi
printf '%s\n' 'CCT desinstalado. No se han modificado datos de cuentas ni logs de auditoría en los homes.'
