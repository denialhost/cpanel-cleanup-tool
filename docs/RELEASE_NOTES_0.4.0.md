# CCT v0.4.0 · WHM root + auditoría

Mini changelog público.

## Qué hay de nuevo

- **Panel WHM solo root:** lista cuentas, analiza y borra candidatos dentro del home del cliente (misma confirmación `ELIMINAR`). No toca rutas del sistema.
- **Auditoría mensual por cuenta:** cada borrado (cPanel o WHM) queda en `~/.cct/audit/YYYY-MM.log` (quién, cuándo, qué, resultado). WHM puede consultar el mes actual.
- El motor Python se reutiliza desde la instalación Jupiter; el instalador registra también el addon WHM.

## Capturas (demo ficticia)

- [Listado de cuentas WHM](screenshots/whm-accounts-current.jpg)
- [Cuenta abierta / plan de limpieza](screenshots/whm-account-current.jpg)
- [Auditoría mensual](screenshots/whm-audit-current.jpg)

Estas imágenes son de la demostración local; no corresponden a un servidor real.

## Actualizar

Como root, sin desinstalar primero:

```bash
curl --proto '=https' --tlsv1.2 -fsSL https://github.com/denialhost/cpanel-cleanup-tool/releases/download/v0.4.0/install-cct.sh -o /root/install-cct.sh
bash /root/install-cct.sh 0.4.0
```

Si falla el registro, el instalador restaura la versión anterior.

Tras instalar: **WHM → Plugins → CCT Limpieza de espacio** (solo root) y **cPanel → Archivos → Limpieza de espacio** (Feature Manager `cct`).
