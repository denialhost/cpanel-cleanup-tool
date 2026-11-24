# CCT v0.4.4 · Content-Type HTML en WHM

## Que corrige
- El marco WHM se servia como `text/plain` y Firefox mostraba el codigo fuente.
- Ahora imprime `Content-type: text/html; charset=utf-8` antes de la plantilla.

## Actualizar
```bash
curl --proto '=https' --tlsv1.2 -fsSL https://github.com/denialhost/cpanel-cleanup-tool/releases/download/v0.4.4/install-cct.sh -o /root/install-cct.sh
bash /root/install-cct.sh 0.4.4
```
