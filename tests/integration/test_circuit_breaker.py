import pytest
import httpx
from app.core.circuit_breaker import db_breaker, redis_breaker, CircuitState


class TestCircuitBreakerIntegration:
    def test_health_check_returns_healthy(self, http_client, auth_headers):
        resp = http_client.get("/api/v1/health", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["database"] == "healthy"
        assert data["redis"] == "healthy"

    def test_response_time_header_on_health_check(self, http_client, auth_headers):
        resp = http_client.get("/api/v1/health", headers=auth_headers)
        assert resp.status_code == 200
        assert "X-Response-Time" in resp.headers
        assert resp.headers["X-Response-Time"].endswith("ms")

    def test_db_breaker_state_is_closed_initially(self):
        assert db_breaker.state == CircuitState.CLOSED

    def test_redis_breaker_state_is_closed_initially(self):
        assert redis_breaker.state == CircuitState.CLOSED

    def test_health_check_returns_200_with_valid_db(self, http_client, auth_headers):
        resp = http_client.get("/api/v1/health", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["database"] == "healthy"
