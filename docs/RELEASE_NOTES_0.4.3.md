# CCT v0.4.3 · Panel dentro del chrome WHM

## Qué corrige

- El addon ya no se abre como página suelta: usa la plantilla WHM (`master.tmpl`) con la app en un iframe.
- El job `publish` del CI deja de fallar al re-subir assets del release (`--clobber`).

## Actualizar

```bash
curl --proto '=https' --tlsv1.2 -fsSL https://github.com/denialhost/cpanel-cleanup-tool/releases/download/v0.4.3/install-cct.sh -o /root/install-cct.sh
bash /root/install-cct.sh 0.4.3
```

Entra desde **WHM → Plugins → CCT Limpieza de espacio** (solo root).
