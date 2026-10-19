#!/bin/bash
set -euo pipefail
if [[ "$(id -u)" != 0 ]]; then
    echo 'La desinstalación requiere root.' >&2
    exit 1
fi
TARGET=/usr/local/cpanel/base/frontend/jupiter/cct
if [[ -L "$TARGET" || ! -f "$TARGET/install.json" ]]; then
    echo 'No se encontró una instalación válida de CCT.' >&2
    exit 1
fi
/usr/local/cpanel/scripts/uninstall_plugin "$TARGET" --theme=jupiter
# Only named plugin files; never recurse or touch any customer home directory.
for filename in index.live.pl index.live.cgi cct_core.py cct_mail.py cct_backend.py cct_live.py python-path index.html styles.css app.js model.js mail.js mail-model.js activity.js app.mjs model.mjs mail.mjs mail-model.mjs icon.svg install.json uninstall.json VERSION uninstall.sh; do
    rm -f -- "$TARGET/$filename"
done
rmdir -- "$TARGET" 2>/dev/null || true
printf '%s\n' 'CCT desinstalado. No se han modificado datos de cuentas.'
