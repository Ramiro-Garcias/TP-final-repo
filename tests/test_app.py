import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as georiesgo


class GeoRiesgoApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        georiesgo.DATABASE = Path(self.temp_dir.name) / "test.db"
        georiesgo.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        georiesgo.initialize_database()
        self.client = georiesgo.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def register(self, username="analista"):
        return self.client.post(
            "/api/register",
            json={"username": username, "email": f"{username}@example.org", "password": "clave-segura-123"},
        )

    def test_registration_assessment_and_event_crud(self):
        self.assertEqual(self.client.get("/api/regions").status_code, 401)
        response = self.register()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json["user"]["role"], "admin")

        regions = self.client.get("/api/regions").json["regions"]
        self.assertEqual(len(regions), 5)
        region = regions[0]
        payload = {
            "region_id": region["id"],
            "event_type": "Inundación",
            "severity": 4,
            "description": "Anegamiento observado en el sector bajo.",
            "occurred_on": "2026-11-16",
            "latitude": region["latitude"],
            "longitude": region["longitude"],
            "title": "Anegamiento de prueba",
            "impact": 5,
            "source": "Observación de campo",
        }
        created = self.client.post("/api/events", json=payload)
        self.assertEqual(created.status_code, 201)
        event_id = created.json["id"]

        assessment = self.client.post(f"/api/assessments/{region['id']}")
        self.assertEqual(assessment.status_code, 200)
        self.assertIn(assessment.json["category"], {"Baja", "Moderada", "Alta", "Crítica"})
        self.assertEqual(assessment.json["model_source"], "Clasificador local explicable")
        self.assertEqual(
            set(assessment.json["risk_factors"]),
            {"Inundación", "Erosión", "Inestabilidad del terreno", "Vulnerabilidad general"},
        )
        self.assertTrue(all(0 <= score <= 100 for score in assessment.json["risk_factors"].values()))
        self.assertIn("no analiza imágenes satelitales", assessment.json["report"])
        invalid_risks = self.client.post(f"/api/assessments/{region['id']}", json={"risks": ["wildfire"]})
        self.assertEqual(invalid_risks.status_code, 400)
        pdf = self.client.get(f"/api/assessments/{assessment.json['id']}/pdf")
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf.mimetype, "application/pdf")
        self.assertEqual(len(self.client.get("/api/assessments").json["assessments"]), 1)

        payload["description"] = "Descripción actualizada."
        self.assertEqual(self.client.put(f"/api/events/{event_id}", json=payload).status_code, 200)
        listed = self.client.get("/api/events").json["events"]
        self.assertEqual(listed[0]["description"], "Descripción actualizada.")
        self.assertEqual(self.client.delete(f"/api/events/{event_id}").status_code, 200)
        self.assertEqual(self.client.get("/api/events").json["events"], [])
        self.assertEqual(listed[0]["title"], "Anegamiento de prueba")
        self.assertEqual(listed[0]["source"], "Observación de campo")
        self.assertEqual(listed[0]["impact"], 5)

    def test_analyst_cannot_edit_another_users_event(self):
        self.assertEqual(self.register("admin").status_code, 201)
        region_id = self.client.get("/api/regions").json["regions"][0]["id"]
        created = self.client.post(
            "/api/events",
            json={"region_id": region_id, "event_type": "Sequía", "severity": 2,
                  "description": "Observación", "occurred_on": "2026-11-16"},
        )
        event_id = created.json["id"]
        self.client.post("/api/logout")
        self.assertEqual(self.register("analista2").json["user"]["role"], "analyst")
        response = self.client.delete(f"/api/events/{event_id}")
        self.assertEqual(response.status_code, 403)

    def test_registration_validation(self):
        response = self.client.post("/api/register", json={"username": "ab", "email": "ab@example.org", "password": "short"})
        self.assertEqual(response.status_code, 400)

    def test_login_accepts_username_case_variations(self):
        self.assertEqual(self.register("Chorizeante").status_code, 201)
        self.client.post("/api/logout")
        response = self.client.post(
            "/api/login",
            json={"username": "chorizeante", "password": "clave-segura-123"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["user"]["username"], "Chorizeante")
        wrong_password = self.client.post(
            "/api/login",
            json={"username": "CHORIZEANTE", "password": "clave-incorrecta"},
        )
        self.assertEqual(wrong_password.status_code, 401)

    def test_session_secret_persists_between_app_starts(self):
        secret_path = Path(self.temp_dir.name) / ".secret"
        first_key = georiesgo.load_secret_key(secret_path)
        second_key = georiesgo.load_secret_key(secret_path)
        self.assertEqual(first_key, second_key)

    def test_local_password_reset_restores_login(self):
        self.assertEqual(self.register("Chorizeante").status_code, 201)
        self.client.post("/api/logout")
        self.assertTrue(georiesgo.set_user_password("chorizeante", "nueva-clave-segura"))
        response = self.client.post(
            "/api/login",
            json={"username": "CHORIZEANTE", "password": "nueva-clave-segura"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(georiesgo.set_user_password("no-existe", "nueva-clave-segura"))

    def test_geography_endpoint_validates_coordinates_and_returns_profile(self):
        self.assertEqual(self.register().status_code, 201)
        profile = {"latitude": 0, "longitude": -30, "water_body": "Atlantic Ocean"}
        with patch("app.fetch_geography", return_value=profile) as fetch_geography:
            response = self.client.get("/api/geography?latitude=0&longitude=-30")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json["geography"]["water_body"], "Atlantic Ocean")
            fetch_geography.assert_called_once_with(0, -30)
        invalid = self.client.get("/api/geography?latitude=91&longitude=-30")
        self.assertEqual(invalid.status_code, 400)

    def test_dashboard_profile_and_geographic_layer_catalog(self):
        self.assertEqual(self.register().status_code, 201)
        dashboard = self.client.get("/api/dashboard")
        self.assertEqual(dashboard.status_code, 200)
        self.assertEqual(dashboard.json["region_count"], 5)
        self.assertEqual(set(dashboard.json["risk_distribution"]), {"Baja", "Moderada", "Alta", "Crítica"})
        self.assertEqual(self.client.get("/api/profile").json["user"]["username"], "analista")
        updated = self.client.put("/api/profile", json={"full_name": "Nombre Analista", "email": "analista@example.org"})
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json["user"]["email"], "analista@example.org")
        layers = self.client.get("/api/layers")
        self.assertEqual(layers.status_code, 200)
        self.assertIn("Zonas inundables oficiales", {layer["name"] for layer in layers.json["layers"]})

    def test_non_admin_cannot_manage_users_or_publish_layers(self):
        self.assertEqual(self.register("admin").status_code, 201)
        self.client.post("/api/logout")
        self.assertEqual(self.register("analista").status_code, 201)
        self.assertEqual(self.client.get("/api/users").status_code, 403)
        response = self.client.post("/api/layers", json={"name": "Prueba", "type": "GeoJSON", "source": "test", "geojson": {}})
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
