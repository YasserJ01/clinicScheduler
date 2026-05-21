import uuid
import httpx
import pytest
from tests.conftest import BASE_URL


class TestMetricsEndpoint:
    """Test Prometheus metrics endpoint."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.client = httpx.Client(base_url=BASE_URL, timeout=10.0)
        self.token = self._register_and_login()
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def teardown_method(self):
        self.client.close()

    def test_metrics_endpoint_returns_prometheus_format(self):
        resp = self.client.get("/api/v1/metrics")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers.get("content-type", "")
        body = resp.text
        assert "# HELP" in body
        assert "# TYPE" in body

    def test_metrics_contains_request_counter(self):
        resp = self.client.get("/api/v1/metrics")
        assert "http_requests_total" in resp.text

    def test_metrics_contains_duration_histogram(self):
        resp = self.client.get("/api/v1/metrics")
        assert "http_request_duration_seconds" in resp.text

    def test_metrics_increments_on_request(self):
        self.client.get("/api/v1/doctors", headers=self.headers)

        resp = self.client.get("/api/v1/metrics")
        assert 'method="GET"' in resp.text
        assert 'endpoint="/api/v1/doctors"' in resp.text

    def _register_and_login(self):
        username = f"metrics_{uuid.uuid4().hex[:8]}"
        resp = self.client.post(
            "/api/v1/auth/register",
            json={"username": username, "password": "test1234"},
        )
        assert resp.status_code == 200
        return resp.json()["access_token"]
