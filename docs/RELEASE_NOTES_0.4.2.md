# CCT v0.4.2 · Estilos WHM con cpsess

## Qué corrige

- El HTML WHM pedía CSS/JS en `/cgi/addon_cct.cgi?...`, fuera del prefijo `/cpsess…/`, así que la página salía sin estilos y las APIs fallaban.
- Ahora usa URLs relativas a la CGI actual (`?action=asset…` y `location.pathname`).

## Actualizar

```bash
curl --proto '=https' --tlsv1.2 -fsSL https://github.com/denialhost/cpanel-cleanup-tool/releases/download/v0.4.2/install-cct.sh -o /root/install-cct.sh
bash /root/install-cct.sh 0.4.2
```

Abre el icono desde WHM (Plugins / buscador). Recarga forzada si tenías la pestaña abierta.
