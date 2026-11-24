# CCT v0.4.8 · CI en Python 3.9

## Que corrige
- `tools/bundle_whm_app.py` usaba `Path.write_text(..., newline=)`, incompatible con Python 3.9 del workflow.

## Actualizar
```bash
curl --proto '=https' --tlsv1.2 -fsSL https://github.com/denialhost/cpanel-cleanup-tool/releases/download/v0.4.8/install-cct.sh -o /root/install-cct.sh
bash /root/install-cct.sh 0.4.8
```
