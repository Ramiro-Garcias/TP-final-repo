import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import app as georiesgo
import seismic

NOW = datetime(2026, 11, 17, tzinfo=timezone.utc)
CSV_HEADER = "time,latitude,longitude,depth,mag,magType,nst,gap,dmin,rms,net,id,updated,place,type\n"


def make_csv(rows):
    lines = [CSV_HEADER]
    for index, (year, lat, lon, depth, mag) in enumerate(rows):
        lines.append(f"{year}-03-01T10:00:00.000Z,{lat},{lon},{depth},{mag},mww,,,,,us,us{index},2020,\"{index} km N of Prueba\",earthquake\n")
    return "".join(lines)


def geojson(rows):
    return json.dumps({"features": [
        {"id": f"sig{i}", "properties": {"mag": m, "place": "Gran sismo", "time": int(datetime(y, 1, 1, tzinfo=timezone.utc).timestamp() * 1000),
                                         "url": f"https://earthquake.usgs.gov/earthquakes/eventpage/sig{i}", "tsunami": 0, "mmi": 7.1},
         "geometry": {"coordinates": [lo, la, d]}} for i, (y, la, lo, d, m) in enumerate(rows)]})


# Sismicidad tipo subducción: muchos sismos medianos, algunos profundos, y dos sismos grandes históricos
MAIN_ROWS = [(1980 + i % 40, -33.0 + (i % 7) * 0.2, -68.5 + (i % 5) * 0.2, 100 if i % 2 else 15, 4.5 + (i % 12) * 0.1) for i in range(120)]
SIG_ROWS = [(1985, -33.1, -68.7, 15, 6.4), (1944, -31.5, -68.6, 20, 7.0), (2010, -35.9, -72.7, 35, 8.8)]


def fake_http_get(url, headers=None, timeout=25):
    if "open-meteo" in url:
        return json.dumps({"elevation": [760, 790, 740, 765, 760]})
    if "/count" in url:
        return "42"
    if "format=geojson" in url:
        return geojson(SIG_ROWS)
    return make_csv(MAIN_ROWS)


class SeismicEngineTests(unittest.TestCase):
    def test_haversine_known_distance(self):
        self.assertAlmostEqual(seismic.haversine_km(0, 0, 0, 1), 111.19, delta=0.3)

    def test_pga_decreases_with_distance_and_grows_with_magnitude(self):
        self.assertGreater(seismic.pga_median_g(7, 20, 15), seismic.pga_median_g(7, 100, 15))
        self.assertGreater(seismic.pga_median_g(7.5, 50, 15), seismic.pga_median_g(6.0, 50, 15))

    def test_mmi_is_monotonic_and_bounded(self):
        values = [seismic.pga_to_mmi(g) for g in (0.005, 0.05, 0.1, 0.3, 1.0, 5.0)]
        self.assertEqual(values, sorted(values))
        self.assertTrue(all(1.0 <= v <= 10.0 for v in values))

    def test_hazard_class_thresholds(self):
        self.assertEqual([seismic.hazard_class(x) for x in (0.05, 0.1, 0.2, 0.4)], ["Baja", "Moderada", "Alta", "Muy alta"])

    def test_b_value_needs_enough_events(self):
        self.assertEqual(seismic.estimate_b_value([5.0] * 10), (1.0, False))
        value, estimated = seismic.estimate_b_value([4.5 + (i % 10) * 0.1 for i in range(200)])
        self.assertTrue(estimated)
        self.assertTrue(0.6 <= value <= 1.5)

    def test_quiet_zone_scores_low_and_active_zone_scores_higher(self):
        quiet = seismic.analyze_catalog(-31.4, -64.2, [], [], NOW)
        self.assertEqual(quiet["hazard"]["class_gshap"], "Baja")
        quiet_score, quiet_category, _ = seismic.score_risk(quiet)
        self.assertEqual(quiet_category, "Baja")
        main = seismic.parse_usgs_csv(make_csv(MAIN_ROWS), -33.0, -68.5)
        sig = seismic.parse_usgs_geojson(geojson(SIG_ROWS), -33.0, -68.5)
        active = seismic.analyze_catalog(-33.0, -68.5, main, sig, NOW)
        active_score, _, _ = seismic.score_risk(active)
        self.assertGreater(active_score, quiet_score)
        self.assertGreaterEqual(active["max_magnitude_regional"]["magnitude"], 8.8)

    def test_completeness_excludes_old_small_events(self):
        old_small = [seismic._event("a", "1960-01-01T00:00:00Z", -33, -68, 10, 5.0, "x", -33, -68)]
        self.assertEqual(seismic.hazard_events(old_small, [], 2026), [])

    def test_scenario_tool_clamps_inputs(self):
        result = seismic.tool_scenario({"magnitud": 99, "distancia_km": -5, "profundidad_km": 9999})
        self.assertEqual(result["magnitud"], 9.5)
        self.assertEqual(result["distancia_epicentral_km"], 0.0)
        self.assertEqual(result["profundidad_km"], 700.0)

    def test_official_sources_put_national_agency_first(self):
        self.assertIn("INPRES", seismic.official_sources("AR")[0]["name"])
        self.assertEqual(seismic.official_sources(None)[0]["name"], seismic.GLOBAL_SOURCES[0]["name"])


class SeismicAnalysisTests(unittest.TestCase):
    def test_analysis_without_ai_uses_local_report_and_real_catalog_fields(self):
        with patch.object(seismic, "http_get", fake_http_get):
            result = seismic.analyze_point(-33.0, -68.5, {"place": "Mendoza", "country": "Argentina", "country_code": "AR"}, None, NOW)
        self.assertEqual(result["model_source"], "Clasificador local explicable")
        self.assertIn("Resumen", result["report"])
        self.assertEqual(result["catalog"]["n_events_main"], len([e for e in MAIN_ROWS]))
        self.assertTrue(result["sources"][0]["url"].startswith("https://earthquake.usgs.gov/"))
        self.assertTrue(any("INPRES" in s["name"] for s in result["official_sources"]))
        self.assertEqual(result["terrain"]["elevation_m"], 760)

    def test_usgs_failure_falls_back_to_emsc(self):
        emsc_payload = json.dumps({"features": [{"id": "e1", "properties": {"mag": 5.1, "time": "2015-05-05T00:00:00Z", "depth": 30, "flynn_region": "X"},
                                                 "geometry": {"coordinates": [-68.5, -33.0, -30]}}]})

        def flaky(url, headers=None, timeout=25):
            if "earthquake.usgs.gov" in url:
                raise OSError("sin conexión")
            if "seismicportal" in url:
                return emsc_payload
            return fake_http_get(url)

        with patch.object(seismic, "http_get", flaky):
            result = seismic.analyze_point(-33.0, -68.5, {}, None, NOW)
        self.assertTrue(result["sources"][0]["name"].startswith("EMSC"))
        self.assertEqual(result["catalog"]["n_events_main"], 1)

    def test_all_catalogs_down_raises(self):
        with patch.object(seismic, "http_get", side_effect=OSError("down")):
            with self.assertRaises(seismic.SeismicDataError):
                seismic.analyze_point(-33.0, -68.5, {}, None, NOW)


class AgentTests(unittest.TestCase):
    def evidence(self):
        with patch.object(seismic, "http_get", fake_http_get):
            return seismic.analyze_point(-33.0, -68.5, {"country_code": "AR", "place": "Mendoza"}, None, NOW)

    def test_agent_calls_tools_then_writes_final_report(self):
        responses = [
            {"candidates": [{"content": {"role": "model", "parts": [
                {"functionCall": {"name": "buscar_sismos", "args": {"radio_km": 500, "magnitud_minima": 7, "desde_anio": 1900}}},
                {"functionCall": {"name": "simular_escenario", "args": {"magnitud": 7.5, "distancia_km": 40, "profundidad_km": 20}}},
                {"functionCall": {"name": "listar_fuentes_oficiales", "args": {}}}]}}]},
            {"candidates": [{"content": {"role": "model", "parts": [{"text": "Resumen\nInforme final de prueba."}]}}]},
        ]
        sent = []

        def fake_post(url, body, headers=None, timeout=40):
            sent.append((url, body, headers))
            return responses[len(sent) - 1]

        with patch.object(seismic, "http_get", fake_http_get):
            text, model, trace = seismic.run_agent(self.evidence(), "KEY", model="gemini-test", post=fake_post)
        self.assertEqual(text, "Resumen\nInforme final de prueba.")
        self.assertEqual([t["tool"] for t in trace], ["buscar_sismos", "simular_escenario", "listar_fuentes_oficiales"])
        self.assertIn("42 sismos", trace[0]["summary"])
        self.assertEqual(sent[0][2], {"x-goog-api-key": "KEY"})  # la clave va por header, no en la URL
        self.assertNotIn("KEY", sent[0][0])
        second_turn = sent[1][1]["contents"]
        self.assertEqual(second_turn[-1]["parts"][0]["functionResponse"]["name"], "buscar_sismos")

    def test_agent_failure_falls_back_to_local_report(self):
        def broken_post(*args, **kwargs):
            raise OSError("API caída")

        with patch.object(seismic, "http_get", fake_http_get):
            result = seismic.analyze_point(-33.0, -68.5, {"country_code": "AR"}, "KEY", NOW, post=broken_post)
        self.assertEqual(result["model_source"], "Clasificador local explicable")
        self.assertIn("ai_error", result)
        self.assertIn("Resumen", result["report"])

    def test_default_model_is_not_the_retired_one(self):
        self.assertNotEqual(seismic.DEFAULT_GEMINI_MODEL, "gemini-2.0-flash")


class SeismicApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        georiesgo.DATABASE = Path(self.temp_dir.name) / "test.db"
        georiesgo.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        georiesgo.initialize_database()
        self.client = georiesgo.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def login(self, username):
        return self.client.post("/api/register", json={"username": username, "email": f"{username}@example.org", "password": "clave-segura-123"})

    def analyze(self, payload=None):
        geography = {"place": "Mendoza", "country": "Argentina", "country_code": "AR", "water_body": None, "elevation_m": 760}
        with patch.object(seismic, "http_get", fake_http_get), patch.object(georiesgo, "fetch_geography", return_value=geography), \
                patch.dict("os.environ", {"GEMINI_API_KEY": ""}):
            return self.client.post("/api/seismic/analyze", json={"latitude": -33.0, "longitude": -68.5} if payload is None else payload)

    def test_requires_login_and_validates_coordinates(self):
        self.assertEqual(self.client.post("/api/seismic/analyze", json={"latitude": 1, "longitude": 1}).status_code, 401)
        self.login("admin")
        for bad in ({}, {"latitude": 95, "longitude": 0}, {"latitude": "x", "longitude": 0}, {"latitude": 0, "longitude": 181}):
            self.assertEqual(self.analyze(bad).status_code, 400)

    def test_analysis_is_saved_listed_downloaded_and_isolated_per_user(self):
        self.login("admin")
        response = self.analyze()
        self.assertEqual(response.status_code, 201)
        analysis_id = response.json["id"]
        self.assertIn(response.json["analysis"]["category"], ("Baja", "Moderada", "Alta", "Crítica"))

        listing = self.client.get("/api/seismic").json["analyses"]
        self.assertEqual(listing[0]["id"], analysis_id)
        self.assertEqual(self.client.get(f"/api/seismic/{analysis_id}").json["analysis"]["point"]["place"], "Mendoza")
        pdf = self.client.get(f"/api/seismic/{analysis_id}/pdf")
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.data.startswith(b"%PDF"))

        # otro usuario (analista) no ve el análisis ajeno
        admin_client = self.client
        georiesgo_client = georiesgo.app.test_client()
        admin_client.post("/api/users", json={"username": "otro", "email": "otro@example.org", "password": "clave-segura-123", "role": "analyst"})
        georiesgo_client.post("/api/login", json={"username": "otro", "password": "clave-segura-123"})
        self.assertEqual(georiesgo_client.get("/api/seismic").json["analyses"], [])
        self.assertEqual(georiesgo_client.get(f"/api/seismic/{analysis_id}").status_code, 404)
        self.assertEqual(georiesgo_client.get(f"/api/seismic/{analysis_id}/pdf").status_code, 404)

    def test_catalog_outage_returns_502(self):
        self.login("admin")
        geography = {"place": "X", "country": "Y", "country_code": "AR", "water_body": None}
        with patch.object(seismic, "http_get", side_effect=OSError("down")), patch.object(georiesgo, "fetch_geography", return_value=geography):
            response = self.client.post("/api/seismic/analyze", json={"latitude": -33.0, "longitude": -68.5})
        self.assertEqual(response.status_code, 502)


if __name__ == "__main__":
    unittest.main()
