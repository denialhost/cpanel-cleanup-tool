# Cambios

## 0.2.0

Primera publicación para pruebas en servidores cPanel/WHM con Jupiter.

- Cuota, uso y espacio libre de la cuenta; estimación inmediata del ahorro y sus porcentajes.
- Selección de candidatos en papelera, logs rotados, respaldos antiguos y spam/papelera de correo.
- Limpieza de correos por cuenta y fecha, con vista previa y confirmación de borrado definitivo.
- Intento de refresco de la caché de uso de disco de cPanel y aviso si el cambio tarda en aparecer.
- Instalador y actualizador descargable, paquete versionado y verificación SHA-256.
- Pruebas automáticas en Linux y publicación de paquetes mediante GitHub Actions.

La integración real con cPanel y Dovecot requiere validación en un servidor de prueba. La integración con WHMCS todavía no está implementada.
