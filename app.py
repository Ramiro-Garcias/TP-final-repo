import argparse
import getpass
import json
import math
import os
import secrets
import sqlite3
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse
from xml.sax.saxutils import escape as escape_xml

from flask import Flask, g, jsonify, render_template, request, session
from dotenv import load_dotenv
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
from werkzeug.security import check_password_hash, generate_password_hash

import seismic


BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "georiesgo.db"
load_dotenv(BASE_DIR / ".env")
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
SECRET_KEY_PATH = BASE_DIR / ".georiesgo_secret"


def load_secret_key(secret_path=SECRET_KEY_PATH):
    configured_key = os.environ.get("GEORIESGO_SECRET")
    if configured_key:
        return configured_key
    if secret_path.exists():
        existing_key = secret_path.read_text(encoding="utf-8").strip()
        if existing_key:
            return existing_key
    generated_key = secrets.token_hex(32)
    try:
        secret_path.write_text(generated_key, encoding="utf-8")
    except OSError:
        return generated_key
    return generated_key


app = Flask(__name__)
app.secret_key = load_secret_key()
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("COOKIE_SECURE", "0") == "1"


class PostgresCursor:
    def __init__(self, cursor, lastrowid=None):
        self._cursor = cursor
        self.lastrowid = lastrowid

    def fetchone(self):
        row = self._cursor.fetchone()
        return row

    def fetchall(self):
        return self._cursor.fetchall()


class PostgresConnection:
    def __init__(self, connection):
        self._connection = connection

    def execute(self, query, parameters=()):
        import psycopg
        from psycopg.rows import dict_row

        query = query.replace("?", "%s")
        is_insert = query.lstrip().lower().startswith("insert into") and " returning " not in query.lower()
        if is_insert:
            query = query.rstrip().rstrip(";") + " RETURNING id"
        cursor = self._connection.cursor(row_factory=dict_row)
        cursor.execute(query, parameters)
        lastrowid = None
        if is_insert:
            inserted = cursor.fetchone()
            lastrowid = inserted["id"] if inserted else None
        return PostgresCursor(cursor, lastrowid)

    def commit(self):
        self._connection.commit()

    def close(self):
        self._connection.close()


def open_database(path=None):
    if DATABASE_URL:
        import psycopg
        return PostgresConnection(psycopg.connect(DATABASE_URL))
    connection = sqlite3.connect(path or DATABASE)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection

def get_db():
    if "db" not in g:
        g.db = open_database()
    return g.db


@app.teardown_appcontext
def close_db(_error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def initialize_database():
    if DATABASE_URL:
        initialize_postgres_database()
        return
    db = sqlite3.connect(DATABASE)
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            full_name TEXT NOT NULL DEFAULT '',
            email TEXT NOT NULL DEFAULT '',
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('admin', 'analyst')),
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS seismic_analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            place TEXT NOT NULL DEFAULT '',
            country TEXT NOT NULL DEFAULT '',
            score INTEGER NOT NULL,
            category TEXT NOT NULL,
            pga_475 REAL NOT NULL,
            max_magnitude REAL NOT NULL,
            evidence_json TEXT NOT NULL,
            report TEXT NOT NULL,
            model_source TEXT NOT NULL,
            created_by INTEGER NOT NULL REFERENCES users(id),
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS regions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            province TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            longitude REAL NOT NULL,
            latitude REAL NOT NULL,
            geometry_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS terrain_indicators (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            region_id INTEGER NOT NULL UNIQUE REFERENCES regions(id) ON DELETE CASCADE,
            elevation_m REAL NOT NULL,
            slope_pct REAL NOT NULL,
            annual_rainfall_mm REAL NOT NULL,
            flood_exposure REAL NOT NULL,
            erosion_exposure REAL NOT NULL,
            soil_stability REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS environmental_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            region_id INTEGER NOT NULL REFERENCES regions(id) ON DELETE CASCADE,
            event_type TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            severity INTEGER NOT NULL CHECK (severity BETWEEN 1 AND 5),
            description TEXT NOT NULL,
            occurred_on TEXT NOT NULL,
            latitude REAL,
            longitude REAL,
            source TEXT NOT NULL DEFAULT '',
            impact INTEGER NOT NULL DEFAULT 3,
            photo_path TEXT,
            created_by INTEGER NOT NULL REFERENCES users(id),
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS vulnerability_assessments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            region_id INTEGER NOT NULL REFERENCES regions(id) ON DELETE CASCADE,
            score INTEGER NOT NULL,
            category TEXT NOT NULL,
            indicators_json TEXT NOT NULL,
            risks_json TEXT NOT NULL DEFAULT '[]',
            sources_json TEXT NOT NULL DEFAULT '[]',
            data_quality TEXT NOT NULL DEFAULT '',
            report TEXT NOT NULL,
            model_source TEXT NOT NULL,
            created_by INTEGER NOT NULL REFERENCES users(id),
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS region_source_data (
            region_id INTEGER PRIMARY KEY REFERENCES regions(id) ON DELETE CASCADE,
            metadata_json TEXT NOT NULL,
            features_json TEXT NOT NULL,
            dem_samples_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS geographic_layers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            layer_type TEXT NOT NULL,
            source TEXT NOT NULL,
            data_date TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            geojson TEXT NOT NULL DEFAULT '{}',
            is_demo INTEGER NOT NULL DEFAULT 0,
            enabled INTEGER NOT NULL DEFAULT 1
        );
        """
    )
    ensure_sqlite_columns(db)
    seed_layer_catalog(db)
    if db.execute("PRAGMA user_version").fetchone()[0] < 2:
        demo_names = ("Delta del Paraná", "Sierras de Córdoba", "Cuenca del Salado", "Gran Mendoza", "Valle de Lerma")
        placeholders = ",".join("?" for _ in demo_names)
        db.execute(f"DELETE FROM regions WHERE name IN ({placeholders})", demo_names)
        db.execute("PRAGMA user_version = 2")
    if db.execute("SELECT COUNT(*) FROM regions").fetchone()[0] == 0:
        demo_regions = [
            ("Delta del Paraná", "Buenos Aires", -58.65, -34.25, 12, 1.2, 1100, 0.82, 0.28, 0.72),
            ("Sierras de Córdoba", "Córdoba", -64.55, -31.50, 720, 18.0, 780, 0.38, 0.74, 0.58),
            ("Cuenca del Salado", "Buenos Aires", -60.40, -36.30, 45, 0.4, 920, 0.88, 0.34, 0.68),
            ("Gran Mendoza", "Mendoza", -68.85, -32.90, 760, 7.5, 220, 0.24, 0.41, 0.63),
            ("Valle de Lerma", "Salta", -65.45, -24.80, 1180, 5.2, 650, 0.46, 0.52, 0.57),
        ]
        for name, province, longitude, latitude, elevation, slope, rainfall, flood, erosion, stability in demo_regions:
            cursor = db.execute(
                "INSERT INTO regions (name, province, longitude, latitude) VALUES (?, ?, ?, ?)",
                (name, province, longitude, latitude),
            )
            db.execute(
                "UPDATE regions SET description = ?, geometry_json = ? WHERE id = ?",
                ("Punto de referencia de demostración; no es un límite administrativo.",
                 json.dumps({"type": "Point", "coordinates": [longitude, latitude]}), cursor.lastrowid),
            )
            db.execute(
                """INSERT INTO terrain_indicators
                   (region_id, elevation_m, slope_pct, annual_rainfall_mm, flood_exposure,
                    erosion_exposure, soil_stability) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (cursor.lastrowid, elevation, slope, rainfall, flood, erosion, stability),
            )
    db.commit()
    db.close()


def ensure_sqlite_columns(db):
    existing = {row[1] for row in db.execute("PRAGMA table_info(regions)").fetchall()}
    for column, definition in (("description", "TEXT NOT NULL DEFAULT ''"), ("geometry_json", "TEXT NOT NULL DEFAULT '{}'")):
        if column not in existing:
            db.execute(f"ALTER TABLE regions ADD COLUMN {column} {definition}")
    existing = {row[1] for row in db.execute("PRAGMA table_info(environmental_events)").fetchall()}
    for column, definition in (("title", "TEXT NOT NULL DEFAULT ''"), ("source", "TEXT NOT NULL DEFAULT ''"), ("impact", "INTEGER NOT NULL DEFAULT 3"), ("photo_path", "TEXT")):
        if column not in existing:
            db.execute(f"ALTER TABLE environmental_events ADD COLUMN {column} {definition}")
    existing = {row[1] for row in db.execute("PRAGMA table_info(users)").fetchall()}
    for column in ("full_name", "email"):
        if column not in existing:
            db.execute(f"ALTER TABLE users ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
    if "is_active" not in existing:
        db.execute("ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
    existing = {row[1] for row in db.execute("PRAGMA table_info(vulnerability_assessments)").fetchall()}
    if "risks_json" not in existing:
        db.execute("ALTER TABLE vulnerability_assessments ADD COLUMN risks_json TEXT NOT NULL DEFAULT '[]'")
    for column in ("sources_json", "data_quality"):
        if column not in existing:
            db.execute(f"ALTER TABLE vulnerability_assessments ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
    existing = {row[1] for row in db.execute("PRAGMA table_info(geographic_layers)").fetchall()}
    if "is_demo" not in existing:
        db.execute("ALTER TABLE geographic_layers ADD COLUMN is_demo INTEGER NOT NULL DEFAULT 0")
    for row in db.execute("SELECT id, longitude, latitude FROM regions WHERE geometry_json = '{}' OR description = ''").fetchall():
        db.execute(
            "UPDATE regions SET geometry_json=?, description=? WHERE id=?",
            (json.dumps({"type": "Point", "coordinates": [row[1], row[2]]}),
             "Punto de referencia de demostración; no es un límite administrativo.", row[0]),
        )


def seed_layer_catalog(db):
    layers = [
        ("Modelo de elevación global", "raster", "AWS Terrain Tiles / Mapzen Terrarium; fuentes SRTM, USGS, NOAA y regionales", "", "Teselas globales coloreadas por altura/profundidad; resolución variable.", 0),
        ("Mosaico SAOCOM Buenos Aires 2022", "WMS", "CONAE · saocom_mosaicos:BuenosAires_2022", "2022", "Mosaico satelital regional; cobertura limitada a Buenos Aires.", 0),
        ("Vulnerabilidad territorial precargada", "GeoJSON", "Indicadores internos de demostración", "", "Cinco regiones argentinas. No son mediciones oficiales.", 1),
        ("Zonas inundables oficiales", "pendiente", "Sin servicio oficial integrado", "", "La aplicación no dispone actualmente de polígonos oficiales de inundación conectados.", 0),
        ("Erosión observada", "pendiente", "Sin capa nacional oficial integrada", "", "Los porcentajes precargados son demostrativos; no son detecciones observadas.", 1),
        ("Eventos registrados por usuarios", "GeoJSON", "Base de datos GeoRiesgo", "actualizado al consultar", "Registros colaborativos; la ubicación puede aproximarse al centro regional.", 0),
    ]
    for name, layer_type, source, data_date, description, is_demo in layers:
        placeholder = "%s" if DATABASE_URL else "?"
        db.execute(
            f"INSERT INTO geographic_layers (name, layer_type, source, data_date, description, geojson, is_demo) VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, '{{}}', {placeholder}) ON CONFLICT(name) DO NOTHING",
            (name, layer_type, source, data_date, description, is_demo),
        )


def initialize_postgres_database():
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as db:
        statements = [
            "CREATE TABLE IF NOT EXISTS users (id BIGSERIAL PRIMARY KEY, username TEXT NOT NULL UNIQUE, full_name TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT '', password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK (role IN ('admin','analyst')), is_active BOOLEAN NOT NULL DEFAULT TRUE, created_at TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS regions (id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL UNIQUE, province TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', longitude DOUBLE PRECISION NOT NULL, latitude DOUBLE PRECISION NOT NULL, geometry_json TEXT NOT NULL DEFAULT '{}')",
            "CREATE TABLE IF NOT EXISTS terrain_indicators (id BIGSERIAL PRIMARY KEY, region_id BIGINT NOT NULL UNIQUE REFERENCES regions(id) ON DELETE CASCADE, elevation_m DOUBLE PRECISION NOT NULL, slope_pct DOUBLE PRECISION NOT NULL, annual_rainfall_mm DOUBLE PRECISION NOT NULL, flood_exposure DOUBLE PRECISION NOT NULL, erosion_exposure DOUBLE PRECISION NOT NULL, soil_stability DOUBLE PRECISION NOT NULL)",
            "CREATE TABLE IF NOT EXISTS environmental_events (id BIGSERIAL PRIMARY KEY, region_id BIGINT NOT NULL REFERENCES regions(id) ON DELETE CASCADE, event_type TEXT NOT NULL, title TEXT NOT NULL DEFAULT '', severity INTEGER NOT NULL CHECK (severity BETWEEN 1 AND 5), description TEXT NOT NULL, occurred_on TEXT NOT NULL, latitude DOUBLE PRECISION, longitude DOUBLE PRECISION, source TEXT NOT NULL DEFAULT '', impact INTEGER NOT NULL DEFAULT 3, photo_path TEXT, created_by BIGINT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS vulnerability_assessments (id BIGSERIAL PRIMARY KEY, region_id BIGINT NOT NULL REFERENCES regions(id) ON DELETE CASCADE, score INTEGER NOT NULL, category TEXT NOT NULL, indicators_json TEXT NOT NULL, risks_json TEXT NOT NULL DEFAULT '[]', sources_json TEXT NOT NULL DEFAULT '[]', data_quality TEXT NOT NULL DEFAULT '', report TEXT NOT NULL, model_source TEXT NOT NULL, created_by BIGINT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS seismic_analyses (id BIGSERIAL PRIMARY KEY, latitude DOUBLE PRECISION NOT NULL, longitude DOUBLE PRECISION NOT NULL, place TEXT NOT NULL DEFAULT '', country TEXT NOT NULL DEFAULT '', score INTEGER NOT NULL, category TEXT NOT NULL, pga_475 DOUBLE PRECISION NOT NULL, max_magnitude DOUBLE PRECISION NOT NULL, evidence_json TEXT NOT NULL, report TEXT NOT NULL, model_source TEXT NOT NULL, created_by BIGINT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS region_source_data (region_id BIGINT PRIMARY KEY REFERENCES regions(id) ON DELETE CASCADE, metadata_json TEXT NOT NULL, features_json TEXT NOT NULL, dem_samples_json TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS geographic_layers (id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL UNIQUE, layer_type TEXT NOT NULL, source TEXT NOT NULL, data_date TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '', geojson TEXT NOT NULL DEFAULT '{}', is_demo INTEGER NOT NULL DEFAULT 0, enabled INTEGER NOT NULL DEFAULT 1)",
        ]
        for statement in statements:
            db.execute(statement)
        db.execute("ALTER TABLE regions ADD COLUMN IF NOT EXISTS description TEXT NOT NULL DEFAULT ''")
        db.execute("ALTER TABLE regions ADD COLUMN IF NOT EXISTS geometry_json TEXT NOT NULL DEFAULT '{}'")
        db.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS full_name TEXT NOT NULL DEFAULT ''")
        db.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS email TEXT NOT NULL DEFAULT ''")
        db.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE")
        db.execute("ALTER TABLE environmental_events ADD COLUMN IF NOT EXISTS title TEXT NOT NULL DEFAULT ''")
        db.execute("ALTER TABLE environmental_events ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT ''")
        db.execute("ALTER TABLE environmental_events ADD COLUMN IF NOT EXISTS impact INTEGER NOT NULL DEFAULT 3")
        db.execute("ALTER TABLE environmental_events ADD COLUMN IF NOT EXISTS photo_path TEXT")
        db.execute("ALTER TABLE vulnerability_assessments ADD COLUMN IF NOT EXISTS risks_json TEXT NOT NULL DEFAULT '[]'")
        db.execute("ALTER TABLE vulnerability_assessments ADD COLUMN IF NOT EXISTS sources_json TEXT NOT NULL DEFAULT '[]'")
        db.execute("ALTER TABLE vulnerability_assessments ADD COLUMN IF NOT EXISTS data_quality TEXT NOT NULL DEFAULT ''")
        db.execute("ALTER TABLE geographic_layers ADD COLUMN IF NOT EXISTS is_demo INTEGER NOT NULL DEFAULT 0")
        db.execute("UPDATE regions SET description='Punto de referencia de demostración; no es un límite administrativo.' WHERE description=''")
        db.execute("UPDATE regions SET geometry_json=json_build_object('type','Point','coordinates',json_build_array(longitude,latitude))::text WHERE geometry_json='{}'")
        seed_layer_catalog(PostgresConnection(db))
        row = db.execute("SELECT COUNT(*) AS count FROM regions").fetchone()
        if row["count"] == 0:
            seed_postgres_regions(db)


def seed_postgres_regions(db):
    demo_regions = [
        ("Delta del Paraná", "Buenos Aires", "Delta fluvial; indicadores de demostración.", -58.65, -34.25, 12, 1.2, 1100, 0.82, 0.28, 0.72),
        ("Sierras de Córdoba", "Córdoba", "Relieve serrano; indicadores de demostración.", -64.55, -31.50, 720, 18.0, 780, 0.38, 0.74, 0.58),
        ("Cuenca del Salado", "Buenos Aires", "Cuenca de llanura; indicadores de demostración.", -60.40, -36.30, 45, 0.4, 920, 0.88, 0.34, 0.68),
        ("Gran Mendoza", "Mendoza", "Piedemonte árido; indicadores de demostración.", -68.85, -32.90, 760, 7.5, 220, 0.24, 0.41, 0.63),
        ("Valle de Lerma", "Salta", "Valle intermontano; indicadores de demostración.", -65.45, -24.80, 1180, 5.2, 650, 0.46, 0.52, 0.57),
    ]
    for name, province, description, longitude, latitude, elevation, slope, rainfall, flood, erosion, stability in demo_regions:
        region = db.execute(
            "INSERT INTO regions (name, province, description, longitude, latitude, geometry_json) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (name, province, description, longitude, latitude, json.dumps({"type": "Point", "coordinates": [longitude, latitude]})),
        ).fetchone()
        db.execute(
            "INSERT INTO terrain_indicators (region_id, elevation_m, slope_pct, annual_rainfall_mm, flood_exposure, erosion_exposure, soil_stability) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (region["id"], elevation, slope, rainfall, flood, erosion, stability),
        )


def login_required(handler):
    @wraps(handler)
    def wrapped(*args, **kwargs):
        if "user_id" not in session or not current_user():
            session.clear()
            return jsonify(error="Iniciá sesión para continuar."), 401
        return handler(*args, **kwargs)

    return wrapped


def role_required(*allowed_roles):
    def decorate(handler):
        @wraps(handler)
        @login_required
        def wrapped(*args, **kwargs):
            user = current_user()
            if not user or user["role"] not in allowed_roles:
                return jsonify(error="No tenés permisos para realizar esta acción."), 403
            return handler(*args, **kwargs)
        return wrapped
    return decorate


def current_user():
    row = get_db().execute(
        "SELECT id, username, role FROM users WHERE id = ? AND is_active = 1" if not DATABASE_URL else "SELECT id, username, role FROM users WHERE id = %s AND is_active = TRUE",
        (session.get("user_id"),),
    ).fetchone()
    return dict(row) if row else None


def fetch_json(url):
    request_object = urllib.request.Request(url, headers={"User-Agent": "GeoRiesgo/1.0"})
    with urllib.request.urlopen(request_object, timeout=8) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_geography(latitude, longitude):
    coordinates = f"latitude={latitude}&longitude={longitude}"
    urls = {
        "elevation": f"https://api.open-meteo.com/v1/elevation?{coordinates}",
        "weather": (
            "https://api.open-meteo.com/v1/forecast?"
            f"{coordinates}&current=temperature_2m,relative_humidity_2m,wind_speed_10m&timezone=auto&cell_selection=nearest"
        ),
        "marine": f"https://marine-api.open-meteo.com/v1/marine?{coordinates}&current=sea_surface_temperature",
        "place": (
            "https://api.bigdatacloud.net/data/reverse-geocode-client?"
            f"latitude={latitude}&longitude={longitude}&localityLanguage=en"
        ),
    }
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {name: executor.submit(fetch_json, url) for name, url in urls.items()}
        results = {}
        for name, future in futures.items():
            try:
                results[name] = future.result()
            except (OSError, TimeoutError, json.JSONDecodeError, KeyError):
                results[name] = {}

    place_data = results["place"]
    informative = place_data.get("localityInfo", {}).get("informative", [])
    water_terms = ("ocean", "sea", "channel", "gulf", "strait", "bay")
    water_body = next(
        (item.get("name") for item in informative if any(term in item.get("name", "").lower() for term in water_terms)),
        None,
    )
    country = place_data.get("countryName") or None
    locality = place_data.get("city") or place_data.get("locality") or place_data.get("principalSubdivision")
    current = results["weather"].get("current", {})
    marine_current = results["marine"].get("current", {})
    elevation_values = results["elevation"].get("elevation", [])
    return {
        "latitude": latitude,
        "longitude": longitude,
        "place": water_body if not country and water_body else locality or water_body or "Lugar sin nombre en el mapa",
        "country": country,
        "country_code": place_data.get("countryCode") or None,
        "water_body": water_body,
        "elevation_m": elevation_values[0] if elevation_values else None,
        "temperature_c": current.get("temperature_2m"),
        "sea_surface_temperature_c": marine_current.get("sea_surface_temperature"),
        "relative_humidity_pct": current.get("relative_humidity_2m"),
        "wind_speed_kmh": current.get("wind_speed_10m"),
        "time": current.get("time"),
        "timezone": results["weather"].get("timezone"),
        "sources": {
            "elevation": "Copernicus DEM GLO-90 · Open-Meteo",
            "weather": "Modelos meteorológicos globales · Open-Meteo",
            "place": "OpenStreetMap · BigDataCloud",
            "marine": "Open-Meteo Marine",
        },
    }


def set_user_password(username, password):
    if len(password) < 8:
        raise ValueError("La contraseña debe tener al menos 8 caracteres.")
    if DATABASE_URL:
        import psycopg
        with psycopg.connect(DATABASE_URL) as db:
            cursor = db.cursor()
            cursor.execute(
                "UPDATE users SET password_hash = %s WHERE lower(username) = lower(%s)",
                (generate_password_hash(password), username.strip()),
            )
            return cursor.rowcount > 0
    db = sqlite3.connect(DATABASE)
    cursor = db.execute(
        "UPDATE users SET password_hash = ? WHERE username = ? COLLATE NOCASE",
        (generate_password_hash(password), username.strip()),
    )
    db.commit()
    db.close()
    return cursor.rowcount > 0


def calculate_vulnerability(indicators, event_rows):
    recent_severity = min(1.0, sum(row["severity"] for row in event_rows) / 15)
    score = round(
        100
        * (
            indicators["flood_exposure"] * 0.32
            + indicators["erosion_exposure"] * 0.25
            + (1 - indicators["soil_stability"]) * 0.20
            + min(indicators["slope_pct"] / 35, 1) * 0.13
            + recent_severity * 0.10
        )
    )
    category = "Crítica" if score >= 70 else "Alta" if score >= 50 else "Moderada" if score >= 30 else "Baja"
    return score, category, recent_severity


def calculate_risk_factors(indicators, event_rows):
    slope = min(indicators["slope_pct"] / 35, 1)
    rainfall = min(indicators["annual_rainfall_mm"] / 2000, 1)
    event_severity = min(sum(row["severity"] for row in event_rows) / 15, 1)
    return {
        "Inundación": round(100 * (indicators["flood_exposure"] * 0.7 + rainfall * 0.3)),
        "Erosión": round(100 * (indicators["erosion_exposure"] * 0.7 + slope * 0.3)),
        "Inestabilidad del terreno": round(100 * ((1 - indicators["soil_stability"]) * 0.65 + slope * 0.35)),
        "Eventos registrados": round(100 * event_severity),
    }


def generate_report(region, indicators, score, category, recent_severity, risk_factors, data_quality):
    factor_summary = ", ".join(f"{name}: {value}/100" for name, value in risk_factors.items())
    prompt = (
        "Redactá un informe breve en español sobre vulnerabilidad territorial. "
        "No afirmes que estos datos son oficiales ni predigas eventos; distinguí indicadores de hechos. "
        "Si la calidad de datos es baja o los indicadores son de demostración, advertí que no alcanza para concluir riesgo local. "
        "No digas que analizaste píxeles o imágenes satelitales: solo recibís indicadores locales y eventos registrados. "
        f"Región: {region['name']} ({region['province']}). Puntaje: {score}/100 ({category}). "
        f"Elevación demostrativa: {indicators['elevation_m']} m; pendiente: {indicators['slope_pct']}%; "
        f"lluvia anual de referencia: {indicators['annual_rainfall_mm']} mm; "
        f"exposición a inundación: {indicators['flood_exposure']:.2f}; "
        f"exposición a erosión: {indicators['erosion_exposure']:.2f}; "
        f"estabilidad del suelo: {indicators['soil_stability']:.2f}; "
        f"severidad relativa de eventos registrados: {recent_severity:.2f}. "
        f"Factores derivados: {factor_summary}. Calidad/evidencia: {data_quality}. "
        "Incluí factores que explican el resultado y dos medidas preventivas generales."
    )
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return generate_report_without_ai(region, indicators, score, category, recent_severity, risk_factors, data_quality), "Clasificador local explicable"

    model = os.environ.get("GEMINI_MODEL") or seismic.DEFAULT_GEMINI_MODEL
    url = seismic.GEMINI_URL.format(model=model)
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", "x-goog-api-key": api_key})
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8"))
        text = payload["candidates"][0]["content"]["parts"][0]["text"].strip()
        return text, f"Gemini ({model})"
    except (urllib.error.URLError, KeyError, IndexError, json.JSONDecodeError, TimeoutError):
        return "No se pudo consultar Gemini. " + generate_report_without_ai(region, indicators, score, category, recent_severity, risk_factors, data_quality), "Clasificador local explicable"


def generate_report_without_ai(region, indicators, score, category, recent_severity, risk_factors, data_quality):
    factor_summary = ", ".join(f"{name.lower()} ({value}/100)" for name, value in risk_factors.items())
    return (
        f"{region['name']} presenta vulnerabilidad {category.lower()} ({score}/100). "
        f"El cálculo pondera exposición a inundación ({indicators['flood_exposure']:.2f}), "
        f"erosión ({indicators['erosion_exposure']:.2f}), estabilidad del suelo "
        f"({indicators['soil_stability']:.2f}), pendiente y eventos registrados "
        f"(índice {recent_severity:.2f}). Factores derivados: {factor_summary}. "
        "Se recomienda revisar drenajes y conservar cobertura vegetal. "
        f"Calidad de evidencia: {data_quality}. Análisis orientativo; no analiza imágenes satelitales ni reemplaza estudios técnicos o alertas oficiales."
    )


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/session")
def session_info():
    user = current_user() if session.get("user_id") else None
    return jsonify(user=user)


@app.post("/api/register")
def register():
    data = request.get_json(silent=True) or {}
    username = str(data.get("username", "")).strip()
    full_name = str(data.get("full_name", "")).strip()[:120]
    email = str(data.get("email", "")).strip().lower()[:254]
    password = str(data.get("password", ""))
    if len(username) < 3 or len(username) > 40 or len(password) < 8:
        return jsonify(error="Usá un nombre de 3 a 40 caracteres y una contraseña de 8 o más."), 400
    if not email or "@" not in email or "." not in email.rsplit("@", 1)[-1]:
        return jsonify(error="Ingresá un correo electrónico válido para crear la cuenta."), 400
    db = get_db()
    username_query = "SELECT 1 FROM users WHERE lower(username) = lower(?)" if not DATABASE_URL else "SELECT 1 FROM users WHERE lower(username) = lower(%s)"
    if db.execute(username_query, (username,)).fetchone():
        return jsonify(error="Ese nombre de usuario ya está registrado."), 409
    try:
        role = "admin" if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0 else "analyst"
        cursor = db.execute(
            "INSERT INTO users (username, full_name, email, password_hash, role, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (username, full_name, email, generate_password_hash(password), role, datetime.now(timezone.utc).isoformat()),
        )
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify(error="Ese nombre de usuario ya está registrado."), 409
    session.clear()
    session["user_id"] = cursor.lastrowid
    return jsonify(user=current_user()), 201


@app.post("/api/login")
def login():
    data = request.get_json(silent=True) or {}
    username_query = "SELECT * FROM users WHERE lower(username) = lower(?)" if not DATABASE_URL else "SELECT * FROM users WHERE lower(username) = lower(%s)"
    user = get_db().execute(
        username_query.replace("SELECT *", "SELECT *"),
        (str(data.get("username", "")).strip(),),
    ).fetchone()
    if not user or not user["is_active"] or not check_password_hash(user["password_hash"], str(data.get("password", ""))):
        return jsonify(error="Usuario o contraseña incorrectos."), 401
    session.clear()
    session["user_id"] = user["id"]
    return jsonify(user=current_user())


@app.get("/api/geography")
@login_required
def geography():
    try:
        latitude = float(request.args.get("latitude", ""))
        longitude = float(request.args.get("longitude", ""))
    except ValueError:
        return jsonify(error="Indicá latitud y longitud válidas."), 400
    if not math.isfinite(latitude) or not math.isfinite(longitude) or not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        return jsonify(error="La latitud debe estar entre -90 y 90 y la longitud entre -180 y 180."), 400
    return jsonify(geography=fetch_geography(latitude, longitude))


@app.post("/api/logout")
@login_required
def logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/regions")
@login_required
def regions():
    rows = get_db().execute(
        """SELECT r.*, t.elevation_m, t.slope_pct, t.annual_rainfall_mm,
                  t.flood_exposure, t.erosion_exposure, t.soil_stability
           FROM regions r JOIN terrain_indicators t ON t.region_id = r.id ORDER BY r.name"""
    ).fetchall()
    output = []
    for row in rows:
        item = dict(row)
        try:
            item["geometry"] = json.loads(item.pop("geometry_json", "{}"))
        except (TypeError, json.JSONDecodeError):
            item["geometry"] = {}
        events = get_db().execute(
            "SELECT severity FROM environmental_events WHERE region_id = ?", (item["id"],)
        ).fetchall()
        item["event_count"] = len(events)
        item["score"], item["category"], _ = calculate_vulnerability(item, events)
        output.append(item)
    return jsonify(regions=output)


@app.get("/api/dashboard")
@login_required
def dashboard():
    db = get_db()
    user = current_user()
    regions_data = db.execute("SELECT id, name, province FROM regions ORDER BY name").fetchall()
    events_query = "SELECT e.*, r.name AS region_name FROM environmental_events e JOIN regions r ON r.id=e.region_id"
    assessments_query = """SELECT a.*, r.name AS region_name, u.username AS created_by_name
        FROM vulnerability_assessments a JOIN regions r ON r.id=a.region_id
        JOIN users u ON u.id=a.created_by"""
    if user["role"] != "admin":
        events_query += " WHERE e.created_by = ?" if not DATABASE_URL else " WHERE e.created_by = %s"
        assessments_query += " WHERE a.created_by = ?" if not DATABASE_URL else " WHERE a.created_by = %s"
        events = db.execute(events_query + " ORDER BY e.id DESC", (user["id"],)).fetchall()
        assessments = db.execute(assessments_query + " ORDER BY a.id DESC", (user["id"],)).fetchall()
    else:
        events = db.execute(events_query + " ORDER BY e.id DESC").fetchall()
        assessments = db.execute(assessments_query + " ORDER BY a.id DESC").fetchall()
    categories = {"Baja": 0, "Moderada": 0, "Alta": 0, "Crítica": 0}
    scores = []
    for row in regions_data:
        indicators_row = db.execute("SELECT * FROM terrain_indicators WHERE region_id = ?" if not DATABASE_URL else "SELECT * FROM terrain_indicators WHERE region_id = %s", (row["id"],)).fetchone()
        if not indicators_row:
            continue
        indicator_data = dict(indicators_row)
        event_rows = db.execute("SELECT severity FROM environmental_events WHERE region_id = ?" if not DATABASE_URL else "SELECT severity FROM environmental_events WHERE region_id = %s", (row["id"],)).fetchall()
        score, category, _ = calculate_vulnerability(indicator_data, event_rows)
        categories[category] += 1
        scores.append((score, row["name"], category))
    latest_analyses = [dict(row) for row in assessments[:5]]
    latest_events = [dict(row) for row in events[:5]]
    return jsonify(
        region_count=len(regions_data), event_count=len(events), assessment_count=len(assessments),
        risk_distribution=categories, highest_risk=max(scores, default=(0, "Sin datos", "Baja")),
        latest_analyses=latest_analyses, latest_events=latest_events,
    )


@app.route("/api/layers", methods=["GET", "POST"])
@login_required
def geographic_layers():
    db = get_db()
    if request.method == "GET":
        rows = db.execute("SELECT * FROM geographic_layers WHERE enabled = 1 ORDER BY name").fetchall()
        return jsonify(layers=[dict(row) for row in rows])
    user = current_user()
    if user["role"] != "admin":
        return jsonify(error="Solo administración puede publicar capas."), 403
    data = request.get_json(silent=True) or {}
    name = str(data.get("name", "")).strip()
    layer_type = str(data.get("type", "")).strip()
    source = str(data.get("source", "")).strip()
    description = str(data.get("description", "")).strip()
    geojson = data.get("geojson", {})
    is_demo = int(bool(data.get("is_demo", False)))
    if not name or not layer_type or not source or not isinstance(geojson, (dict, list)):
        return jsonify(error="Nombre, tipo, fuente y GeoJSON válido son obligatorios."), 400
    try:
        cursor = db.execute(
            "INSERT INTO geographic_layers (name, layer_type, source, data_date, description, geojson, is_demo) VALUES (?, ?, ?, ?, ?, ?, ?)" if not DATABASE_URL else "INSERT INTO geographic_layers (name, layer_type, source, data_date, description, geojson, is_demo) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (name, layer_type, source, str(data.get("data_date", "")), description, json.dumps(geojson), is_demo),
        )
        db.commit()
    except (sqlite3.IntegrityError, Exception) as error:
        if isinstance(error, sqlite3.IntegrityError) or error.__class__.__name__ == "UniqueViolation":
            return jsonify(error="Ya existe una capa con ese nombre."), 409
        raise
    return jsonify(id=cursor.lastrowid), 201


@app.route("/api/layers/<int:layer_id>", methods=["PUT", "DELETE"])
@role_required("admin")
def update_geographic_layer(layer_id):
    db = get_db()
    placeholder = "%s" if DATABASE_URL else "?"
    if request.method == "DELETE":
        cursor = db.execute(f"DELETE FROM geographic_layers WHERE id = {placeholder}", (layer_id,))
        db.commit()
        return (jsonify(ok=True), 200) if cursor.rowcount else (jsonify(error="Capa no encontrada."), 404)
    data = request.get_json(silent=True) or {}
    fields = {key: data[key] for key in ("name", "layer_type", "source", "data_date", "description", "is_demo", "enabled") if key in data}
    if "geojson" in data:
        fields["geojson"] = json.dumps(data["geojson"])
    if not fields:
        return jsonify(error="No hay cambios para guardar."), 400
    assignments = ", ".join(f"{key} = {placeholder}" for key in fields)
    cursor = db.execute(f"UPDATE geographic_layers SET {assignments} WHERE id = {placeholder}", (*fields.values(), layer_id))
    db.commit()
    return jsonify(ok=True) if cursor.rowcount else (jsonify(error="Capa no encontrada."), 404)


@app.get("/api/profile")
@login_required
def profile():
    user = current_user()
    placeholder = "%s" if DATABASE_URL else "?"
    row = get_db().execute(f"SELECT full_name, email, created_at FROM users WHERE id = {placeholder}", (user["id"],)).fetchone()
    return jsonify(user={**user, **dict(row)})


@app.put("/api/profile")
@login_required
def update_profile():
    data = request.get_json(silent=True) or {}
    full_name = str(data.get("full_name", "")).strip()[:120]
    email = str(data.get("email", "")).strip().lower()[:254]
    if email and ("@" not in email or "." not in email.rsplit("@", 1)[-1]):
        return jsonify(error="Ingresá un correo electrónico válido."), 400
    db = get_db()
    user_id = session["user_id"]
    if data.get("new_password"):
        new_password = str(data["new_password"])
        if len(new_password) < 8:
            return jsonify(error="La nueva contraseña debe tener al menos 8 caracteres."), 400
        db.execute("UPDATE users SET full_name=?, email=?, password_hash=? WHERE id=?",
                   (full_name, email, generate_password_hash(new_password), user_id))
    else:
        db.execute("UPDATE users SET full_name=?, email=? WHERE id=?", (full_name, email, user_id))
    db.commit()
    return profile()


@app.route("/api/users", methods=["GET", "POST"])
@role_required("admin")
def manage_users():
    db = get_db()
    if request.method == "GET":
        users = db.execute("SELECT id, username, full_name, email, role, is_active, created_at FROM users ORDER BY id").fetchall()
        return jsonify(users=[dict(row) for row in users])
    data = request.get_json(silent=True) or {}
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))
    role = str(data.get("role", "analyst"))
    email = str(data.get("email", "")).strip().lower()[:254]
    if len(username) < 3 or len(password) < 8 or role not in {"admin", "analyst"} or not email or "@" not in email or "." not in email.rsplit("@", 1)[-1]:
        return jsonify(error="Usuario, contraseña (mín. 8) o rol inválido."), 400
    name_query = "SELECT 1 FROM users WHERE lower(username)=lower(?)" if not DATABASE_URL else "SELECT 1 FROM users WHERE lower(username)=lower(%s)"
    if db.execute(name_query, (username,)).fetchone():
        return jsonify(error="Ese usuario ya existe."), 409
    try:
        cursor = db.execute(
            "INSERT INTO users (username, full_name, email, password_hash, role, created_at) VALUES (?, ?, ?, ?, ?, ?)" if not DATABASE_URL else "INSERT INTO users (username, full_name, email, password_hash, role, created_at) VALUES (%s, %s, %s, %s, %s, %s)",
            (username, str(data.get("full_name", "")).strip()[:120], email, generate_password_hash(password), role, datetime.now(timezone.utc).isoformat()),
        )
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify(error="Ese usuario ya existe."), 409
    return jsonify(id=cursor.lastrowid), 201


@app.route("/api/users/<int:user_id>", methods=["PUT", "DELETE"])
@role_required("admin")
def update_user(user_id):
    db = get_db()
    placeholder = "%s" if DATABASE_URL else "?"
    if request.method == "DELETE":
        if user_id == session.get("user_id"):
            return jsonify(error="No podés eliminar tu propia cuenta mientras estás conectado."), 400
        target = db.execute(f"SELECT role, is_active FROM users WHERE id = {placeholder}", (user_id,)).fetchone()
        if not target:
            return jsonify(error="Usuario no encontrado."), 404
        if target["role"] == "admin" and target["is_active"]:
            admins = db.execute("SELECT COUNT(*) AS count FROM users WHERE role = 'admin' AND is_active = 1" if not DATABASE_URL else "SELECT COUNT(*) AS count FROM users WHERE role = 'admin' AND is_active = TRUE").fetchone()["count"]
            if admins <= 1:
                return jsonify(error="No se puede eliminar al último administrador."), 400
        db.execute(f"UPDATE users SET is_active = 0 WHERE id = {placeholder}", (user_id,))
        db.commit()
        return jsonify(ok=True)
    data = request.get_json(silent=True) or {}
    role = data.get("role")
    if role not in {"admin", "analyst"}:
        return jsonify(error="El rol debe ser admin o analyst."), 400
    target = db.execute(f"SELECT role, is_active FROM users WHERE id = {placeholder}", (user_id,)).fetchone()
    if not target:
        return jsonify(error="Usuario no encontrado."), 404
    if target["role"] == "admin" and role != "admin":
        admins = db.execute("SELECT COUNT(*) AS count FROM users WHERE role = 'admin' AND is_active = 1" if not DATABASE_URL else "SELECT COUNT(*) AS count FROM users WHERE role = 'admin' AND is_active = TRUE").fetchone()["count"]
        if admins <= 1:
            return jsonify(error="No se puede degradar al último administrador."), 400
    db.execute(f"UPDATE users SET role = {placeholder} WHERE id = {placeholder}", (role, user_id))
    db.commit()
    return jsonify(ok=True)


@app.get("/api/events")
@login_required
def get_events():
    rows = get_db().execute(
        """SELECT e.*, r.name AS region_name, u.username AS created_by_name
           FROM environmental_events e JOIN regions r ON r.id = e.region_id
           JOIN users u ON u.id = e.created_by ORDER BY e.occurred_on DESC, e.id DESC"""
    ).fetchall()
    return jsonify(events=[dict(row) for row in rows])


@app.post("/api/events")
@login_required
def create_event():
    data = request.get_json(silent=True) or {}
    try:
        region_id = int(data.get("region_id"))
        severity = int(data.get("severity"))
        event_type = str(data.get("event_type", "")).strip()
        title = str(data.get("title", event_type)).strip()[:160]
        impact = int(data.get("impact", severity))
        description = str(data.get("description", "")).strip()
        occurred_on = str(data.get("occurred_on", "")).strip()
        source = str(data.get("source", "Reporte de usuario")).strip()[:300]
        photo_path = str(data.get("photo_url", "")).strip()[:1000] or None
        latitude = float(data["latitude"]) if data.get("latitude") not in (None, "") else None
        longitude = float(data["longitude"]) if data.get("longitude") not in (None, "") else None
    except (TypeError, ValueError):
        return jsonify(error="Revisá los valores del evento."), 400
    if severity not in range(1, 6) or impact not in range(1, 6) or not event_type or not title or len(description) > 500 or not occurred_on:
        return jsonify(error="Completá tipo, fecha y descripción; severidad de 1 a 5."), 400
    if photo_path and not photo_path.startswith(("https://", "http://")):
        return jsonify(error="La foto debe ser una URL HTTP o HTTPS."), 400
    if (latitude is None) != (longitude is None) or (latitude is not None and (not math.isfinite(latitude) or not math.isfinite(longitude) or not -90 <= latitude <= 90 or not -180 <= longitude <= 180)):
        return jsonify(error="Ingresá ambas coordenadas dentro de los rangos geográficos válidos."), 400
    if not get_db().execute("SELECT id FROM regions WHERE id = ?", (region_id,)).fetchone():
        return jsonify(error="La región seleccionada no existe."), 404
    cursor = get_db().execute(
          """INSERT INTO environmental_events
              (region_id, event_type, title, severity, description, occurred_on, latitude, longitude, source, impact, photo_path, created_by, created_at)
              VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
          (region_id, event_type, title, severity, description, occurred_on, latitude, longitude, source, impact, photo_path,
         session["user_id"], datetime.now(timezone.utc).isoformat()),
    )
    get_db().commit()
    return jsonify(id=cursor.lastrowid), 201


@app.route("/api/events/<int:event_id>", methods=["PUT", "DELETE"])
@login_required
def update_or_delete_event(event_id):
    db = get_db()
    event = db.execute("SELECT * FROM environmental_events WHERE id = ?", (event_id,)).fetchone()
    user = current_user()
    if not event:
        return jsonify(error="Evento no encontrado."), 404
    if event["created_by"] != user["id"] and user["role"] != "admin":
        return jsonify(error="Solo quien lo registró o una persona administradora puede modificarlo."), 403
    if request.method == "DELETE":
        db.execute("DELETE FROM environmental_events WHERE id = ?", (event_id,))
        db.commit()
        return jsonify(ok=True)
    data = request.get_json(silent=True) or {}
    try:
        severity = int(data.get("severity"))
        impact = int(data.get("impact", severity))
        region_id = int(data.get("region_id"))
        event_type = str(data.get("event_type", "")).strip()
        title = str(data.get("title", event_type)).strip()[:160]
        description = str(data.get("description", "")).strip()
        occurred_on = str(data.get("occurred_on", "")).strip()
        source = str(data.get("source", "Reporte de usuario")).strip()[:300]
        photo_path = str(data.get("photo_url", "")).strip()[:1000] or None
    except (TypeError, ValueError):
        return jsonify(error="Revisá los valores del evento."), 400
    valid_region = db.execute("SELECT id FROM regions WHERE id = ?", (region_id,)).fetchone()
    try:
        latitude = float(data["latitude"]) if data.get("latitude") not in (None, "") else None
        longitude = float(data["longitude"]) if data.get("longitude") not in (None, "") else None
    except (TypeError, ValueError):
        return jsonify(error="Las coordenadas deben ser números válidos."), 400
    valid_coordinates = (latitude is None and longitude is None) or (
        latitude is not None and longitude is not None and math.isfinite(latitude) and math.isfinite(longitude)
        and -90 <= latitude <= 90 and -180 <= longitude <= 180
    )
    if severity not in range(1, 6) or impact not in range(1, 6) or not event_type or not title or not occurred_on or len(description) > 500 or not valid_region or not valid_coordinates:
        return jsonify(error="Revisá los datos: la región debe existir, la severidad es de 1 a 5."), 400
    if photo_path and not photo_path.startswith(("https://", "http://")):
        return jsonify(error="La foto debe ser una URL HTTP o HTTPS."), 400
    cursor = db.execute(
        """UPDATE environmental_events
           SET region_id=?, event_type=?, title=?, severity=?, description=?, occurred_on=?, latitude=?, longitude=?, source=?, impact=?, photo_path=?
           WHERE id=?""",
        (region_id, event_type, title, severity, description, occurred_on, latitude, longitude, source, impact, photo_path, event_id),
    )
    db.commit()
    return jsonify(ok=True)


@app.post("/api/assessments/<int:region_id>")
@login_required
def assess_region(region_id):
    db = get_db()
    region_row = db.execute("SELECT * FROM regions WHERE id = ?", (region_id,)).fetchone()
    if not region_row:
        return jsonify(error="Región no encontrada."), 404
    region = dict(region_row)
    indicators = dict(db.execute("SELECT * FROM terrain_indicators WHERE region_id = ?", (region_id,)).fetchone())
    events = db.execute("SELECT severity, event_type FROM environmental_events WHERE region_id = ?", (region_id,)).fetchall()
    payload = request.get_json(silent=True) or {}
    supported_risks = {"flood", "erosion", "topography", "general"}
    selected_risks = payload.get("risks", ["flood", "erosion", "topography", "general"])
    if not isinstance(selected_risks, list) or not selected_risks or any(risk not in supported_risks for risk in selected_risks):
        return jsonify(error="Seleccioná uno o más riesgos válidos."), 400
    score, category, recent_severity = calculate_vulnerability(indicators, events)
    all_factors = calculate_risk_factors(indicators, events)
    all_factors["Vulnerabilidad general"] = score
    risk_map = {"flood": "Inundación", "erosion": "Erosión", "topography": "Inestabilidad del terreno", "general": "Vulnerabilidad general"}
    risk_factors = {risk_map[key]: all_factors[risk_map[key]] for key in selected_risks}
    score = round(sum(risk_factors.values()) / len(risk_factors))
    category = "Crítica" if score >= 70 else "Alta" if score >= 50 else "Moderada" if score >= 30 else "Baja"
    data_quality = "baja: los indicadores territoriales precargados son demostrativos y no hay capa oficial de inundación/suelo vinculada"
    source_metadata = [
        {"name": "Indicadores territoriales precargados", "source": "GeoRiesgo IA · datos de demostración", "is_demo": True},
        {"name": "Eventos ambientales", "source": "Registro de usuarios GeoRiesgo IA", "event_count": len(events), "is_demo": False},
    ]
    report, model_source = generate_report(region, indicators, score, category, recent_severity, risk_factors, data_quality)
    cursor = db.execute(
        """INSERT INTO vulnerability_assessments
              (region_id, score, category, indicators_json, risks_json, sources_json, data_quality, report, model_source, created_by, created_at)
              VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
          (region_id, score, category, json.dumps(indicators), json.dumps(selected_risks), json.dumps(source_metadata), data_quality, report, model_source,
         session["user_id"], datetime.now(timezone.utc).isoformat()),
    )
    db.commit()
    return jsonify(id=cursor.lastrowid, score=score, category=category, report=report, model_source=model_source,
                   indicators=indicators, risk_factors=risk_factors, selected_risks=selected_risks,
                   data_quality=data_quality, sources=source_metadata)


@app.get("/api/assessments")
@login_required
def assessment_history():
    user = current_user()
    page = max(1, request.args.get("page", default=1, type=int))
    per_page = min(100, max(1, request.args.get("per_page", default=50, type=int)))
    query = """SELECT a.id, a.score, a.category, a.report, a.model_source, a.created_at,
                  a.risks_json, a.sources_json, a.data_quality,
                  r.name AS region_name, u.username AS created_by_name
           FROM vulnerability_assessments a JOIN regions r ON r.id=a.region_id
           JOIN users u ON u.id=a.created_by"""
    params = []
    if user["role"] != "admin":
        query += " WHERE a.created_by = ?" if not DATABASE_URL else " WHERE a.created_by = %s"
        params.append(user["id"])
    category = request.args.get("category")
    if category:
        query += (" AND " if params else " WHERE ") + ("a.category = ?" if not DATABASE_URL else "a.category = %s")
        params.append(category)
    query += " ORDER BY a.id DESC LIMIT ? OFFSET ?" if not DATABASE_URL else " ORDER BY a.id DESC LIMIT %s OFFSET %s"
    params.extend([per_page, (page - 1) * per_page])
    rows = get_db().execute(query, params).fetchall()
    return jsonify(assessments=[dict(row) for row in rows], page=page, per_page=per_page)


@app.get("/api/assessments/<int:assessment_id>")
@login_required
def assessment_detail(assessment_id):
    user = current_user()
    placeholder = "%s" if DATABASE_URL else "?"
    query = f"""SELECT a.*, r.name AS region_name, r.province, r.latitude, r.longitude,
        u.username AS created_by_name FROM vulnerability_assessments a
        JOIN regions r ON r.id=a.region_id JOIN users u ON u.id=a.created_by
        WHERE a.id = {placeholder}"""
    row = get_db().execute(query, (assessment_id,)).fetchone()
    if not row:
        return jsonify(error="Análisis no encontrado."), 404
    result = dict(row)
    if user["role"] != "admin" and result["created_by"] != user["id"]:
        return jsonify(error="No tenés acceso a este análisis."), 403
    result["indicators"] = json.loads(result.pop("indicators_json"))
    result["risks"] = json.loads(result.pop("risks_json", "[]"))
    result["sources"] = json.loads(result.pop("sources_json", "[]"))
    return jsonify(assessment=result)


@app.get("/api/assessments/compare")
@login_required
def compare_assessments():
    raw_ids = request.args.get("ids", "")
    try:
        assessment_ids = list(dict.fromkeys(int(item) for item in raw_ids.split(",") if item.strip()))
    except ValueError:
        return jsonify(error="Los identificadores deben ser números."), 400
    if len(assessment_ids) != 2:
        return jsonify(error="Seleccioná exactamente dos análisis para comparar."), 400
    user = current_user()
    placeholders = ",".join("?" if not DATABASE_URL else "%s" for _ in assessment_ids)
    query = f"""SELECT a.id, a.score, a.category, a.risks_json, a.created_at,
        r.name AS region_name, r.province, a.report, u.username AS created_by_name
        FROM vulnerability_assessments a JOIN regions r ON r.id=a.region_id
        JOIN users u ON u.id=a.created_by WHERE a.id IN ({placeholders}) ORDER BY a.created_at"""
    rows = get_db().execute(query, assessment_ids).fetchall()
    if len(rows) != 2:
        return jsonify(error="No se encontraron ambos análisis."), 404
    results = [dict(row) for row in rows]
    if user["role"] != "admin" and any(item["created_by_name"] != user["username"] for item in results):
        return jsonify(error="Solo podés comparar tus propios análisis."), 403
    for item in results:
        item["risks"] = json.loads(item.pop("risks_json", "[]"))
    return jsonify(analyses=results, score_delta=results[1]["score"] - results[0]["score"])


@app.get("/api/assessments/<int:assessment_id>/pdf")
@login_required
def assessment_pdf(assessment_id):
    from io import BytesIO

    response = assessment_detail(assessment_id)
    if isinstance(response, tuple) or response.status_code != 200:
        return response
    assessment = response.get_json()["assessment"]
    stream = BytesIO()
    document = SimpleDocTemplate(stream, pagesize=A4, title=f"GeoRiesgo - {assessment['region_name']}")
    styles = getSampleStyleSheet()
    story = [Paragraph("GeoRiesgo IA · Informe de análisis territorial", styles["Title"]), Spacer(1, 16)]
    lines = [
        f"Región: {assessment['region_name']} · {assessment['province']}",
        f"Fecha: {assessment['created_at']}",
        f"Coordenadas: {assessment['latitude']}, {assessment['longitude']}",
        f"Riesgos: {', '.join(assessment['risks'])}",
        f"Calidad de evidencia: {assessment['data_quality']}",
        "Fuentes: " + json.dumps(assessment["sources"], ensure_ascii=False),
        f"Vulnerabilidad: {assessment['category']} ({assessment['score']}/100)",
        f"Responsable: {assessment['created_by_name']}",
        f"Motor: {assessment['model_source']}",
        "Variables: " + json.dumps(assessment["indicators"], ensure_ascii=False),
        "Informe: " + assessment["report"],
        "Limitaciones: los indicadores precargados son demostrativos; este informe no es una alerta oficial.",
    ]
    story.extend(Paragraph(escape_xml(str(line)), styles["BodyText"]) for line in lines)
    document.build(story)
    from flask import send_file
    stream.seek(0)
    return send_file(stream, mimetype="application/pdf", as_attachment=True,
                     download_name=f"georiesgo-analisis-{assessment_id}.pdf")


def _valid_coordinates(payload):
    try:
        latitude = float(payload.get("latitude"))
        longitude = float(payload.get("longitude"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(latitude) or not math.isfinite(longitude) or not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        return None
    return latitude, longitude


def _seismic_visible(row):
    user = current_user()
    return bool(row) and (user["role"] == "admin" or row["created_by"] == user["id"])


@app.post("/api/seismic/analyze")
@login_required
def seismic_analyze():
    coordinates = _valid_coordinates(request.get_json(silent=True) or {})
    if not coordinates:
        return jsonify(error="Indicá latitud (-90 a 90) y longitud (-180 a 180) válidas."), 400
    latitude, longitude = coordinates
    geography = fetch_geography(latitude, longitude)
    if geography.get("water_body") and not geography.get("country"):
        geography["place"] = geography["water_body"]
    try:
        evidence = seismic.analyze_point(latitude, longitude, geography, os.environ.get("GEMINI_API_KEY"))
    except seismic.SeismicDataError as error:
        return jsonify(error=str(error)), 502
    point = evidence["point"]
    max_local = evidence["catalog"]["max_magnitude_local"]
    db = get_db()
    cursor = db.execute(
        """INSERT INTO seismic_analyses
              (latitude, longitude, place, country, score, category, pga_475, max_magnitude, evidence_json, report, model_source, created_by, created_at)
              VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (latitude, longitude, point.get("place") or "", point.get("country") or "", evidence["score"], evidence["category"],
         evidence["catalog"]["hazard"]["pga_475_g"], max_local["magnitude"] if max_local else 0.0,
         json.dumps(evidence, ensure_ascii=False), evidence["report"], evidence["model_source"],
         session["user_id"], datetime.now(timezone.utc).isoformat()),
    )
    db.commit()
    return jsonify(id=cursor.lastrowid, analysis=evidence), 201


@app.get("/api/seismic")
@login_required
def seismic_history():
    user = current_user()
    query = (
        "SELECT s.id, s.latitude, s.longitude, s.place, s.country, s.score, s.category, s.pga_475, s.max_magnitude, "
        "s.model_source, s.created_at, u.username AS created_by_name FROM seismic_analyses s JOIN users u ON u.id = s.created_by"
    )
    parameters = ()
    if user["role"] != "admin":
        query += " WHERE s.created_by = ?"
        parameters = (user["id"],)
    rows = get_db().execute(query + " ORDER BY s.id DESC LIMIT 50", parameters).fetchall()
    return jsonify(analyses=[dict(row) for row in rows])


@app.get("/api/seismic/<int:analysis_id>")
@login_required
def seismic_detail(analysis_id):
    row = get_db().execute("SELECT * FROM seismic_analyses WHERE id = ?", (analysis_id,)).fetchone()
    if not _seismic_visible(row):
        return jsonify(error="Análisis no encontrado."), 404
    return jsonify(id=row["id"], analysis=json.loads(row["evidence_json"]))


@app.get("/api/seismic/<int:analysis_id>/pdf")
@login_required
def seismic_pdf(analysis_id):
    from io import BytesIO
    from flask import send_file

    row = get_db().execute("SELECT * FROM seismic_analyses WHERE id = ?", (analysis_id,)).fetchone()
    if not _seismic_visible(row):
        return jsonify(error="Análisis no encontrado."), 404
    evidence = json.loads(row["evidence_json"])
    catalog, point = evidence["catalog"], evidence["point"]
    hazard = catalog["hazard"]
    stream = BytesIO()
    document = SimpleDocTemplate(stream, pagesize=A4, title=f"GeoRiesgo sísmico - {point.get('place') or analysis_id}")
    styles = getSampleStyleSheet()
    header = [
        f"Lugar: {point.get('place') or 'sin nombre'} · {point.get('country') or ''}",
        f"Coordenadas: {point['latitude']}, {point['longitude']} · Fecha: {row['created_at']}",
        f"Riesgo sísmico: {evidence['category']} ({evidence['score']}/100) · Calidad de datos: {evidence['data_quality']}",
        f"PGA 10 % en 50 años: {hazard['pga_475_g']} g (MMI ~{hazard['mmi_475']}) · PGA 2 % en 50 años: {hazard['pga_2475_g']} g · "
        f"Clase GSHAP: {hazard['class_gshap']}",
        f"Magnitud máxima plausible: M{catalog['plausible_max_magnitude']} · Motor: {evidence['model_source']}",
    ]
    story = [Paragraph("GeoRiesgo IA · Informe de riesgo sísmico", styles["Title"]), Spacer(1, 12)]
    story.extend(Paragraph(escape_xml(line), styles["BodyText"]) for line in header)
    story.extend([Spacer(1, 10), Paragraph("Informe", styles["Heading2"])])
    story.extend(Paragraph(escape_xml(line) or "&nbsp;", styles["BodyText"]) for line in evidence["report"].splitlines())
    story.extend([Spacer(1, 10), Paragraph("Sismos mayores del entorno", styles["Heading2"])])
    for event in catalog["top_events"]:
        story.append(Paragraph(escape_xml(
            f"M{event['magnitude']} · {str(event['time'])[:10]} · prof. {event['depth_km']} km · a {event['distance_km']} km · {event['place']}"
        ), styles["BodyText"]))
    story.extend([Spacer(1, 10), Paragraph("Fuentes", styles["Heading2"])])
    for source in evidence["sources"] + evidence["official_sources"]:
        state = "consultada" if source.get("consulted") else "referencia a verificar"
        story.append(Paragraph(escape_xml(f"{source['name']} ({state}): {source['url'].split('?')[0]}"), styles["BodyText"]))
    story.extend([Spacer(1, 10), Paragraph(escape_xml("Método: " + evidence["method"]), styles["BodyText"]),
                  Paragraph(escape_xml("Limitaciones: " + evidence["limitations"]), styles["BodyText"])])
    document.build(story)
    stream.seek(0)
    return send_file(stream, mimetype="application/pdf", as_attachment=True,
                     download_name=f"georiesgo-sismico-{analysis_id}.pdf")


initialize_database()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset-password", metavar="USUARIO")
    arguments = parser.parse_args()
    if arguments.reset_password:
        new_password = getpass.getpass("Nueva contraseña (mínimo 8 caracteres): ")
        confirmation = getpass.getpass("Repetí la nueva contraseña: ")
        if new_password != confirmation:
            parser.error("Las contraseñas no coinciden.")
        try:
            updated = set_user_password(arguments.reset_password, new_password)
        except ValueError as error:
            parser.error(str(error))
        if not updated:
            parser.error("No existe ese usuario.")
        print("Contraseña actualizada. Ya podés iniciar sesión.")
    else:
        app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), debug=False)