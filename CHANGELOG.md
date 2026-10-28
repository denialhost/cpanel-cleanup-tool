# Cambios

## 0.2.5

- Detecta Python 3.9+ en rutas del sistema aunque el Python predeterminado sea antiguo, como puede ocurrir en AlmaLinux 8. Conserva la selección al instalar el paquete descargado y muestra un diagnóstico breve si falta una versión compatible.
- Añade una barra de uso por categoría, cargada por separado, y conserva otra para la proyección del ahorro seleccionado. Mide el correo por bloques físicos sin leer mensajes, con un límite de 8 segundos.
- Excluye los contadores virtuales de correo que cPanel inyecta en sus totales. Oculta cualquier desglose inconsistente y mantiene el ocupado/libre confirmado por la cuota.

- Integra el acceso principal con la plantilla oficial de Jupiter: conserva cabecera, barra lateral y navegación de cPanel. Los enlaces antiguos redirigen al nuevo marco.
- Aumenta la letra de tablas, rutas, filtros, formularios y avisos, con tamaños relativos al navegador y distribución adaptable al espacio disponible.
- Oculta los elementos protegidos en la lista; siguen excluidos del ahorro y del borrado.
- Agrupa la papelera de archivos y el spam y la papelera de correo por cuenta, con cantidad, tamaño total y última modificación. Los filtros se aplican a los elementos antes de agrupar.
- Conserva la selección y las validaciones individuales de cada archivo o mensaje, aunque se presenten como un grupo; la confirmación también resume los grupos.
- Añade regresiones de agrupación, instalación del marco y redirección, y renueva las capturas con datos ficticios.

## 0.2.4

- Muestra una ventana con tres puntos animados al analizar la cuenta, consultar la cuota y preparar el correo. No inventa porcentajes cuando aún no se conoce el total.
- Muestra el porcentaje de elementos procesados durante el borrado, con avance al completar cada grupo y conteos separados de eliminados y pendientes.
- Reduce los resultados a un resumen breve. Los errores se agrupan en detalles plegados, con ejemplos limitados y un informe completo descargable.
- Conserva como texto los componentes de la huella de cada archivo. JavaScript redondeaba las fechas en nanosegundos y podía rechazar selecciones válidas como si los archivos hubieran cambiado.
- Añade pruebas que pasan las huellas por JSON de Node/JavaScript y mantienen el rechazo de archivos modificados, además de regresiones para el progreso y los resultados masivos.
- Renueva las capturas con datos ficticios y escenarios de espera, progreso y errores simulados.

## 0.2.3

- Usa archivos `.js` para los módulos del navegador. En cPanel 138, cpsrvd devolvía los archivos `.mjs` como HTML, por lo que el navegador no los ejecutaba y la interfaz quedaba en «Conectando cuenta…».
- Actualiza las importaciones, el instalador, el desinstalador y la demostración local. Conserva los módulos ES sin dependencias de terceros.
- Incluye la declaración de módulos de Node en el paquete y comprueba que toda la cadena de importaciones del navegador esté presente con extensiones compatibles.
- Comprobado en un navegador con sesión real de cPanel 138.0 build 15: cuenta, cuota, análisis, recarga y lista de correo visibles, sin errores JavaScript. No se ejecutaron borrados de clientes.

## 0.2.2

- Acepta `CONTENT_LENGTH` vacío o ausente en solicitudes sin cuerpo. cpsrvd puede enviarlo vacío al abrir el plugin, lo que provocaba el error genérico de conexión en v0.2.1.
- Mantiene el rechazo de longitudes negativas, no numéricas o superiores a 1 MiB.
- Añade regresiones para la apertura HTML con longitud vacía, la consulta de cuota sin la variable y las longitudes inválidas.
- Verificado en un servidor real con cPanel 138.0 build 15: apertura HTML, cuota y listado de correo, además del acceso de compatibilidad. No se ejecutaron borrados en esa validación.

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
