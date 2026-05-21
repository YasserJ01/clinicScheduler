import uuid
import httpx
import pytest
from tests.conftest import BASE_URL


class TestGDPRExport:
    """Test GDPR data export endpoint."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.client = httpx.Client(base_url=BASE_URL, timeout=10.0)
        self.admin_token = self._register_role("admin")
        self.user_token = self._register_role("patient")
        self.admin_headers = {"Authorization": f"Bearer {self.admin_token}"}
        self.user_headers = {"Authorization": f"Bearer {self.user_token}"}

    def teardown_method(self):
        self.client.close()

    def test_export_requires_admin(self):
        resp = self.client.get(
            "/api/v1/admin/patients/1/export",
            headers=self.user_headers,
        )
        assert resp.status_code == 403

    def test_export_nonexistent_patient(self):
        resp = self.client.get(
            "/api/v1/admin/patients/99999/export",
            headers=self.admin_headers,
        )
        assert resp.status_code == 404

    def test_export_returns_ndjson(self):
        patient = self._create_patient()
        resp = self.client.get(
            f"/api/v1/admin/patients/{patient['id']}/export",
            headers=self.admin_headers,
        )
        assert resp.status_code == 200
        assert "application/x-ndjson" in resp.headers.get("content-type", "")
        lines = resp.text.strip().split("\n")
        assert len(lines) >= 1
        first_line = lines[0]
        data = __import__("json").loads(first_line)
        assert data["type"] == "patient"
        assert data["name"] == patient["name"]

    def _create_patient(self):
        name = f"GDPR Export Test {uuid.uuid4().hex[:8]}"
        email = f"{uuid.uuid4().hex[:8]}@gdpr.com"
        resp = self.client.post(
            "/api/v1/patients",
            json={"name": name, "email": email},
            headers=self.admin_headers,
        )
        assert resp.status_code in (200, 201)
        return resp.json()

    def _register_role(self, role):
        username = f"gdpr_{role}_{uuid.uuid4().hex[:8]}"
        resp = self.client.post(
            "/api/v1/auth/register",
            json={"username": username, "password": "test1234", "role": role},
        )
        assert resp.status_code == 200
        return resp.json()["access_token"]


class TestGDPRAnonymisation:
    """Test GDPR patient anonymisation endpoint."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.client = httpx.Client(base_url=BASE_URL, timeout=10.0)
        self.admin_token = self._register_role("admin")
        self.user_token = self._register_role("patient")
        self.admin_headers = {"Authorization": f"Bearer {self.admin_token}"}
        self.user_headers = {"Authorization": f"Bearer {self.user_token}"}

    def teardown_method(self):
        self.client.close()

    def test_anonymise_requires_admin(self):
        resp = self.client.delete(
            "/api/v1/admin/patients/1",
            headers=self.user_headers,
        )
        assert resp.status_code == 403

    def test_anonymise_nonexistent_patient(self):
        resp = self.client.delete(
            "/api/v1/admin/patients/99999",
            headers=self.admin_headers,
        )
        assert resp.status_code == 404

    def test_anonymise_patient_success(self):
        patient = self._create_patient()
        original_name = patient["name"]
        original_email = patient["email"]

        resp = self.client.delete(
            f"/api/v1/admin/patients/{patient['id']}",
            headers=self.admin_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["patient"]["name"] == f"ANONYMIZED-{patient['id']}"
        assert body["patient"]["email"] == f"anonymized-{patient['id']}@redacted.local"
        assert body["patient"]["phone"] is None

    def test_anonymised_patient_no_longer_identifiable(self):
        patient = self._create_patient()
        original_email = patient["email"]

        self.client.delete(
            f"/api/v1/admin/patients/{patient['id']}",
            headers=self.admin_headers,
        )

        resp = self.client.get(
            "/api/v1/patients",
            headers=self.admin_headers,
        )
        assert resp.status_code == 200
        patients = resp.json()
        for p in patients:
            if p["id"] == patient["id"]:
                assert "ANONYMIZED" in p["name"]
                assert "redacted.local" in p["email"]

    def _create_patient(self):
        name = f"GDPR Anon Test {uuid.uuid4().hex[:8]}"
        email = f"{uuid.uuid4().hex[:8]}@anonymise.com"
        resp = self.client.post(
            "/api/v1/patients",
            json={"name": name, "email": email, "phone": "555-1234"},
            headers=self.admin_headers,
        )
        assert resp.status_code in (200, 201)
        return resp.json()

    def _register_role(self, role):
        username = f"gdpr_anon_{role}_{uuid.uuid4().hex[:8]}"
        resp = self.client.post(
            "/api/v1/auth/register",
            json={"username": username, "password": "test1234", "role": role},
        )
        assert resp.status_code == 200
        return resp.json()["access_token"]
