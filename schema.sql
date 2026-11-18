-- GeoRiesgo IA PostgreSQL schema
-- Apply to an empty database with: psql "$DATABASE_URL" -f schema.sql
-- Runtime initialization is idempotent and can also create these tables.

CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    full_name TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'analyst')),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS regions (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    province TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    longitude DOUBLE PRECISION NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    latitude DOUBLE PRECISION NOT NULL CHECK (latitude BETWEEN -90 AND 90),
    geometry_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS terrain_indicators (
    id BIGSERIAL PRIMARY KEY,
    region_id BIGINT NOT NULL UNIQUE REFERENCES regions(id) ON DELETE CASCADE,
    elevation_m DOUBLE PRECISION NOT NULL,
    slope_pct DOUBLE PRECISION NOT NULL,
    annual_rainfall_mm DOUBLE PRECISION NOT NULL,
    flood_exposure DOUBLE PRECISION NOT NULL CHECK (flood_exposure BETWEEN 0 AND 1),
    erosion_exposure DOUBLE PRECISION NOT NULL CHECK (erosion_exposure BETWEEN 0 AND 1),
    soil_stability DOUBLE PRECISION NOT NULL CHECK (soil_stability BETWEEN 0 AND 1)
);

CREATE TABLE IF NOT EXISTS environmental_events (
    id BIGSERIAL PRIMARY KEY,
    region_id BIGINT NOT NULL REFERENCES regions(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    severity INTEGER NOT NULL CHECK (severity BETWEEN 1 AND 5),
    description TEXT NOT NULL,
    occurred_on TEXT NOT NULL,
    latitude DOUBLE PRECISION CHECK (latitude IS NULL OR latitude BETWEEN -90 AND 90),
    longitude DOUBLE PRECISION CHECK (longitude IS NULL OR longitude BETWEEN -180 AND 180),
    source TEXT NOT NULL DEFAULT '',
    impact INTEGER NOT NULL DEFAULT 3 CHECK (impact BETWEEN 1 AND 5),
    photo_path TEXT,
    created_by BIGINT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    CHECK ((latitude IS NULL) = (longitude IS NULL))
);

CREATE TABLE IF NOT EXISTS vulnerability_assessments (
    id BIGSERIAL PRIMARY KEY,
    region_id BIGINT NOT NULL REFERENCES regions(id) ON DELETE CASCADE,
    score INTEGER NOT NULL CHECK (score BETWEEN 0 AND 100),
    category TEXT NOT NULL,
    indicators_json TEXT NOT NULL,
    risks_json TEXT NOT NULL DEFAULT '[]',
    sources_json TEXT NOT NULL DEFAULT '[]',
    data_quality TEXT NOT NULL DEFAULT '',
    report TEXT NOT NULL,
    model_source TEXT NOT NULL,
    created_by BIGINT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS region_source_data (
    region_id BIGINT PRIMARY KEY REFERENCES regions(id) ON DELETE CASCADE,
    metadata_json TEXT NOT NULL,
    features_json TEXT NOT NULL,
    dem_samples_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS geographic_layers (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    layer_type TEXT NOT NULL,
    source TEXT NOT NULL,
    data_date TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    geojson TEXT NOT NULL DEFAULT '{}',
    is_demo INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_events_region_date ON environmental_events(region_id, occurred_on DESC);
CREATE INDEX IF NOT EXISTS idx_assessments_user_date ON vulnerability_assessments(created_by, created_at DESC);

-- Clearly identified example regions and indicators for a first run.
INSERT INTO regions (name, province, description, longitude, latitude)
VALUES
    ('Delta del Paraná', 'Buenos Aires', 'Indicadores de demostración; punto de referencia, no límite administrativo.', -58.65, -34.25),
    ('Sierras de Córdoba', 'Córdoba', 'Indicadores de demostración; punto de referencia, no límite administrativo.', -64.55, -31.50),
    ('Cuenca del Salado', 'Buenos Aires', 'Indicadores de demostración; punto de referencia, no límite administrativo.', -60.40, -36.30),
    ('Gran Mendoza', 'Mendoza', 'Indicadores de demostración; punto de referencia, no límite administrativo.', -68.85, -32.90),
    ('Valle de Lerma', 'Salta', 'Indicadores de demostración; punto de referencia, no límite administrativo.', -65.45, -24.80)
ON CONFLICT (name) DO NOTHING;

UPDATE regions
SET geometry_json = json_build_object('type', 'Point', 'coordinates', json_build_array(longitude, latitude))::text
WHERE geometry_json = '{}';

INSERT INTO terrain_indicators
    (region_id, elevation_m, slope_pct, annual_rainfall_mm, flood_exposure, erosion_exposure, soil_stability)
SELECT r.id, v.elevation_m, v.slope_pct, v.annual_rainfall_mm, v.flood_exposure, v.erosion_exposure, v.soil_stability
FROM (VALUES
    ('Delta del Paraná', 12.0, 1.2, 1100.0, 0.82, 0.28, 0.72),
    ('Sierras de Córdoba', 720.0, 18.0, 780.0, 0.38, 0.74, 0.58),
    ('Cuenca del Salado', 45.0, 0.4, 920.0, 0.88, 0.34, 0.68),
    ('Gran Mendoza', 760.0, 7.5, 220.0, 0.24, 0.41, 0.63),
    ('Valle de Lerma', 1180.0, 5.2, 650.0, 0.46, 0.52, 0.57)
) AS v(name, elevation_m, slope_pct, annual_rainfall_mm, flood_exposure, erosion_exposure, soil_stability)
JOIN regions r ON r.name = v.name
ON CONFLICT (region_id) DO NOTHING;

INSERT INTO geographic_layers (name, layer_type, source, data_date, description, geojson, is_demo)
VALUES
    ('Modelo de elevación global', 'raster', 'AWS Terrain Tiles / Mapzen Terrarium; fuentes SRTM, USGS, NOAA y regionales', '', 'Teselas globales coloreadas por altura/profundidad; resolución variable.', '{}', 0),
    ('Mosaico SAOCOM Buenos Aires 2022', 'WMS', 'CONAE · saocom_mosaicos:BuenosAires_2022', '2022', 'Mosaico satelital regional; cobertura limitada a Buenos Aires.', '{}', 0),
    ('Vulnerabilidad territorial precargada', 'GeoJSON', 'Indicadores internos de demostración', '', 'Cinco regiones argentinas. No son mediciones oficiales.', '{}', 1),
    ('Zonas inundables oficiales', 'pendiente', 'Sin servicio oficial integrado', '', 'La aplicación no dispone actualmente de polígonos oficiales de inundación conectados.', '{}', 0),
    ('Erosión observada', 'pendiente', 'Sin capa nacional oficial integrada', '', 'Los porcentajes precargados son demostrativos; no son detecciones observadas.', '{}', 1),
    ('Eventos registrados por usuarios', 'GeoJSON', 'Base de datos GeoRiesgo', 'actualizado al consultar', 'Registros colaborativos; la ubicación puede aproximarse al centro regional.', '{}', 0)
ON CONFLICT (name) DO NOTHING;

CREATE TABLE IF NOT EXISTS seismic_analyses (
    id BIGSERIAL PRIMARY KEY,
    latitude DOUBLE PRECISION NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    place TEXT NOT NULL DEFAULT '',
    country TEXT NOT NULL DEFAULT '',
    score INTEGER NOT NULL,
    category TEXT NOT NULL,
    pga_475 DOUBLE PRECISION NOT NULL,
    max_magnitude DOUBLE PRECISION NOT NULL,
    evidence_json TEXT NOT NULL,
    report TEXT NOT NULL,
    model_source TEXT NOT NULL,
    created_by BIGINT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL
);
