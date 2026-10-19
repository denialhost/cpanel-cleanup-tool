# Cambios

## 0.2.1

- Corrige la conexión LiveAPI que podía añadir `Child failed to make LIVEAPI connection to cPanel` detrás del JSON.
- Usa el módulo oficial de Perl para abrir, consultar y cerrar la sesión; el motor Python usa tuberías privadas y deja de ejecutar los clientes de línea de comandos dentro de LiveAPI.
- Mantiene la comprobación de la función `cct` y un acceso de compatibilidad para la URL de v0.2.0.
- Aclara que Feature Manager muestra **Limpieza de espacio Plugin** y puede seleccionarlo por defecto.
- Añade pruebas del ciclo de conexión, respuestas JSON, errores de API y rechazo de funciones deshabilitadas. El proveedor LiveAPI se simula en estas pruebas.
- Incluye las capturas de demostración en el paquete para conservar los enlaces del README.

## 0.2.0

Primera publicación para pruebas en servidores cPanel/WHM con Jupiter.

- Cuota, uso y espacio libre de la cuenta; estimación inmediata del ahorro y sus porcentajes.
- Selección de candidatos en papelera, logs rotados, respaldos antiguos y spam/papelera de correo.
- Limpieza de correos por cuenta y fecha, con vista previa y confirmación de borrado definitivo.
- Intento de refresco de la caché de uso de disco de cPanel y aviso si el cambio tarda en aparecer.
- Instalador y actualizador descargable, paquete versionado y verificación SHA-256.
- Pruebas automáticas en Linux y publicación de paquetes mediante GitHub Actions.

La integración real con cPanel y Dovecot requiere validación en un servidor de prueba. La integración con WHMCS todavía no está implementada.
