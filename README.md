# GeoRiesgo IA

Trabajo Final Integrador · Opción 9 · Análisis inteligente de riesgos territoriales.

GeoRiesgo IA es una aplicación web Flask con API REST, Leaflet/GeoJSON, almacenamiento relacional y generación de informes. PostgreSQL es el motor recomendado para producción; SQLite se mantiene como modo local de cero configuración y para pruebas automatizadas. La primera cuenta de una base vacía es administradora.

## Arquitectura

```mermaid
erDiagram
    USERS ||--o{ ENVIRONMENTAL_EVENTS : registra
    USERS ||--o{ VULNERABILITY_ASSESSMENTS : ejecuta
    REGIONS ||--|| TERRAIN_INDICATORS : describe
    REGIONS ||--o{ ENVIRONMENTAL_EVENTS : ubica
    REGIONS ||--o{ VULNERABILITY_ASSESSMENTS : analiza
    REGIONS ||--o| REGION_SOURCE_DATA : documenta
    USERS { bigint id PK; string username; string email; string password_hash; string role }
    REGIONS { bigint id PK; string name; string province; float latitude; float longitude; json geometry_json }
    TERRAIN_INDICATORS { bigint id PK; bigint region_id FK; float elevation_m; float slope_pct; float flood_exposure; float erosion_exposure }
    ENVIRONMENTAL_EVENTS { bigint id PK; bigint region_id FK; bigint created_by FK; string event_type; int impact; float latitude; float longitude; string source }
    VULNERABILITY_ASSESSMENTS { bigint id PK; bigint region_id FK; bigint created_by FK; int score; string category; json risks_json; text report }
    GEOGRAPHIC_LAYERS { bigint id PK; string name; string layer_type; string source; date data_date; json geojson; boolean is_demo }
```

La aplicación está organizada en `app.py` (REST, sesión, persistencia, análisis, PDF y fuentes), `templates/index.html` (vistas), `static/app.js` (mapa e interacciones), `static/style.css`, `schema.sql` (DDL y datos de prueba), `tests/` y `README.md`. La capa de acceso PostgreSQL utiliza psycopg y mantiene SQLite para desarrollo/pruebas. Las consultas son parametrizadas.

## Instalación local

1. Crear y activar un entorno Python 3.11 o posterior.
2. Instalar dependencias: `python -m pip install -r requirements.txt`.
3. Opcional: copiar `.env.example` a `.env` y configurar `GEORIESGO_SECRET`, `GEMINI_API_KEY` y, si usás PostgreSQL, `DATABASE_URL`.
4. Ejecutar `python app.py` o `python main.py` y elegir la opción 9.
5. Abrir `http://127.0.0.1:5000` y crear una cuenta. En una base nueva, esa primera cuenta recibe rol administrador.

Sin `DATABASE_URL`, la app crea/usa `georiesgo.db` en el directorio del proyecto. Se conserva la base existente; las columnas faltantes se agregan en inicialización. La clave de sesión local se guarda en `.georiesgo_secret`, excluida de Git. En producción, establecé una clave `GEORIESGO_SECRET` estable desde el entorno.

### PostgreSQL

1. Crear una base y usuario PostgreSQL.
2. Aplicar `schema.sql` con `psql "$DATABASE_URL" -f schema.sql` (opcional; Flask también inicializa las tablas).
3. Configurar `DATABASE_URL=postgresql://usuario:contraseña@host:5432/georiesgo` en `.env` o el entorno del hosting.
4. Ejecutar `python app.py`.

La URL de ejemplo debe ser `postgresql://...`; no usar el formato de dialecto SQLAlchemy `postgresql+...`, ya que la capa DB usa psycopg directamente.

## Módulos

- **Dashboard:** contadores, distribución de vulnerabilidad y actividad reciente consultados desde la base, con accesos rápidos.
- **Mapa interactivo:** Leaflet con búsqueda global, mapa base OpenStreetMap/Esri/OpenTopoMap/IGN, coordenadas, elevación y profundidad colorizadas, eventos, exposición demostrativa por inundación/erosión, riesgo local, imagen NASA y capa regional CONAE.
- **Nuevo análisis:** selección de región y factores de inundación, erosión, topografía y vulnerabilidad general; guarda el análisis, muestra evidencia/calidad y genera informe.
- **Eventos ambientales:** crear, consultar, filtrar, modificar o eliminar eventos con tipo, nombre, impacto, fecha, fuente, coordenadas opcionales y URL de foto opcional. Si faltan coordenadas, el mapa utiliza el centro de la región, claramente como aproximación; a escala mundial agrupa eventos cercanos.
- **Historial:** lista paginada (hasta 100 por solicitud), filtro por categoría, detalle, comparación de dos análisis y acceso al PDF. Los usuarios consultan los propios; administración consulta todos.
- **Informes:** acceso al historial y descarga individual de PDF.
- **Usuarios:** exclusivo de administradores; alta, cambio de rol y baja. El último admin y la cuenta activa no se pueden eliminar/degradar por accidente.
- **Configuración:** perfil, email, cambio de contraseña autenticado y catálogo de capas con fuentes/limitaciones.

Los roles son `admin` y `analyst`. Contraseñas con hash Werkzeug; sesión HttpOnly/SameSite=Lax. Recuperación por email no está activada porque requiere proveedor SMTP y verificación de identidad; para recuperación local, cerrar el servidor y ejecutar `python app.py --reset-password NOMBRE_USUARIO` desde PowerShell.

## Riesgo sísmico por click en el mapa (agente IA)

En **Mapa interactivo**, al hacer clic en cualquier punto del mundo y presionar *Analizar riesgo sísmico*, el backend ejecuta este flujo:

1. **Catálogos oficiales**: USGS ComCat (sismos M≥4,5 en 300 km desde 1973 y M≥6 en 600 km desde 1900, con MMI/alerta de tsunami cuando existen). Si USGS falla, usa EMSC como respaldo. Las URLs exactas consultadas se guardan y se muestran en el informe para poder verificarlas.
2. **Terreno**: elevación y pendiente local aproximada (Copernicus DEM GLO-90 vía Open-Meteo).
3. **Peligrosidad empírica**: aceleración pico (PGA) esperada con 10 % y 2 % de probabilidad en 50 años (Joyner & Boore 1981, tasas por completitud del catálogo), intensidad Mercalli (Worden et al. 2012), clase GSHAP, períodos de retorno de M6/M7/M8 (Gutenberg-Richter, estimador de Aki-Utsu) y magnitud máxima plausible.
4. **Puntaje 0-100 auditable** (50 % PGA, 30 % magnitud máxima plausible, 20 % frecuencia de M≥5). Lo calcula el código, no la IA.
5. **Agente Gemini con function calling**: recibe la evidencia y puede *llamar herramientas por su cuenta* (`buscar_sismos` para ampliar radio/magnitud/período en USGS, `simular_escenario` para estimar el efecto de un sismo hipotético, `listar_fuentes_oficiales`), y redacta el informe: resumen, sismicidad histórica, magnitud máxima, si es recomendable vivir ahí, recomendaciones y fuentes. Las herramientas invocadas se muestran en pantalla. Sin `GEMINI_API_KEY` o si la API falla, se genera un informe local con los mismos datos.
6. Se guarda en `seismic_analyses`, se dibujan los sismos en el mapa (tamaño = magnitud, color = profundidad) y se puede descargar en PDF.

Endpoints: `POST /api/seismic/analyze` (`{latitude, longitude}`), `GET /api/seismic`, `GET /api/seismic/<id>`, `GET /api/seismic/<id>/pdf`. Cada usuario ve solo sus análisis; el administrador ve todos.

**Límites que el informe declara:** el catálogo instrumental cubre ~50 años (M≥4,5) y ~125 años (M≥7), por lo que fallas con ciclos más largos pueden no estar representadas; no se conoce el tipo de suelo ni se modelan fallas locales; la atenuación es genérica. Es una estimación orientativa, no un pronóstico ni reemplaza la zonificación oficial (en Argentina, INPRES-CIRSOC 103) ni un estudio de ingeniería. INPRES, GEM y NOAA figuran como *referencias a verificar* porque no exponen una API pública integrada.

> Importante: `gemini-2.0-flash` fue dado de baja por Google el 1/6/2026. El modelo por defecto ahora es `gemini-3.5-flash` (configurable con `GEMINI_MODEL`); consultá la página de deprecaciones de Gemini por si cambia.

## Inteligencia artificial y análisis (informe territorial original)

Con `GEMINI_API_KEY`, el backend solicita a Gemini una interpretación narrativa de los indicadores persistidos y seleccionados. La clave nunca se envía al navegador. Si la clave falta o la API falla, se guarda un informe explicable local. `GEMINI_MODEL` permite seleccionar el modelo.

La IA recibe la puntuación y los factores calculados, no píxeles de las imágenes satelitales. El sistema declara calidad de evidencia baja para los indicadores precargados y advierte que no puede emitir alertas. Los datos no alcanzan para pronosticar eventos ni afirmar que una zona está en peligro.

## Fuentes geográficas y límites

- **OpenStreetMap / Photon:** cartografía y búsqueda mundial. Se muestra atribución; Photon es un servicio comunitario sujeto a limitación de uso.
- **AWS Terrain Tiles / Mapzen Terrarium:** elevación/batimetría global visualizada con tinte hipsométrico. La resolución y fuentes subyacentes varían por lugar; es una capa de visualización, no un DEM de precisión para ingeniería.
- **Copernicus DEM GLO-90 vía Open-Meteo:** elevación puntual aproximada de 90 m.
- **Open-Meteo:** temperatura del aire, humedad, viento y temperatura superficial marina de modelos. Son estimaciones/modelos con marca de tiempo, no observaciones de estación.
- **NASA EOSDIS GIBS:** capa satelital global de reflectancia verdadera.
- **IGN Argenmap:** mapa base argentino.
- **CONAE:** mosaico WMS SAOCOM Buenos Aires 2022; no es cobertura nacional.
- **BigDataCloud:** topónimos/masas de agua inversos.

Cinco regiones e indicadores de inundación, erosión, lluvia, pendiente y estabilidad son **datos de demostración**, no mediciones. La aplicación no tiene polígonos oficiales de inundación ni una capa nacional observada de erosión conectados. El catálogo indica estas ausencias en lugar de simularlos. Los overlays puntuales de inundación/erosión están rotulados como demo. Para uso operativo, cargar GeoJSON/raster oficiales con cobertura, resolución, fecha, licencia y metadatos comprobables. No utilizar el puntaje para respuesta de emergencia, navegación o decisiones de infraestructura.

## API principal

- `POST /api/register`, `POST /api/login`, `POST /api/logout`, `GET /api/session`, `GET/PUT /api/profile`
- `GET /api/dashboard`, `GET /api/regions`, `GET /api/geography?latitude=...&longitude=...`
- `GET/POST /api/events`, `PUT/DELETE /api/events/<id>`
- `POST /api/assessments/<region_id>`, `GET /api/assessments`, `GET /api/assessments/<id>`, `GET /api/assessments/<id>/pdf`, `GET /api/assessments/compare?ids=<id>,<id>`
- `POST /api/seismic/analyze`, `GET /api/seismic`, `GET /api/seismic/<id>`, `GET /api/seismic/<id>/pdf`
- `GET/POST /api/layers`, `PUT/DELETE /api/layers/<id>` (escritura admin)
- `GET/POST /api/users`, `PUT/DELETE /api/users/<id>` (admin)

## Pruebas

Ejecutar `python -m unittest discover -s tests -v`. Las pruebas cubren autenticación, permisos, CRUD, puntuación, PDF, dashboard, perfil, catálogo, validación de coordenadas y persistencia de sesión, más (`tests/test_seismic.py`) el motor sísmico, el respaldo EMSC, el bucle del agente con herramientas (Gemini simulado), el fallback local y las rutas `/api/seismic`.

## Despliegue

No se ha publicado automáticamente. Para hosting: configurar PostgreSQL, `DATABASE_URL`, `GEORIESGO_SECRET`, HTTPS, `COOKIE_SECURE=1`, `GEMINI_API_KEY` si se desea, y servir con un WSGI server (por ejemplo Gunicorn en Linux). Mantener secretos fuera del repositorio, restringir CORS si se agrega, agregar protección CSRF para despliegue web y backups de PostgreSQL. Las fuentes cartográficas y ambientales externas requieren acceso a Internet.
