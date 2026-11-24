# CCT v0.4.5 · Dashboard WHM funcional

## Que corrige
- La interfaz WHM se veía pero no listaba cuentas ni respondía a botones.
- Causa: CSP `script-src 'self'` bloqueaba el importmap; además faltaban `model.js` y `activity.js` en el árbol WHM.

## Actualizar
```bash
curl --proto '=https' --tlsv1.2 -fsSL https://github.com/denialhost/cpanel-cleanup-tool/releases/download/v0.4.5/install-cct.sh -o /root/install-cct.sh
bash /root/install-cct.sh 0.4.5
```
