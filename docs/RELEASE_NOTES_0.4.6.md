# CCT v0.4.6 · Listado WHM y JS funcional

## Que corrige
- ``action=accounts`` fallaba con 500 porque el CGI permitía ``whm/listaccts`` y el backend pedía ``whm/listaccts/list``.
- El dashboard no ejecutaba lógica: ``activity.js`` importaba ``./model.js`` relativo al CGI y acababa en 404.

## Actualizar
```bash
curl --proto '=https' --tlsv1.2 -fsSL https://github.com/denialhost/cpanel-cleanup-tool/releases/download/v0.4.6/install-cct.sh -o /root/install-cct.sh
bash /root/install-cct.sh 0.4.6
```
