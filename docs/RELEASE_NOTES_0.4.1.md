# CCT v0.4.1 · Fix registro WHM

## Qué corrige

- El AppConfig usaba `name=CCT Limpieza de espacio`; cPanel solo permite letras, números, `-` y `_`, así que `register_appconfig` fallaba, el instalador hacía rollback y el icono no aparecía en WHM.
- Ahora: `name=cct` + `displayname=CCT Limpieza de espacio`, y el instalador comprueba `/var/cpanel/apps/cct.conf`.

## Actualizar

```bash
curl --proto '=https' --tlsv1.2 -fsSL https://github.com/denialhost/cpanel-cleanup-tool/releases/download/v0.4.1/install-cct.sh -o /root/install-cct.sh
bash /root/install-cct.sh 0.4.1
```

Luego en WHM busca **CCT Limpieza de espacio** (Plugins o buscador). Solo root.
