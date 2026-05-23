import uuid


class TestRateLimiting:
    def test_rate_limit_headers_present(self, http_client, user_token):
        resp = http_client.get(
            "/api/v1/doctors",
            headers={"Authorization": f"Bearer {user_token}"},
        )
        assert resp.status_code == 200
        assert "X-RateLimit-Limit" in resp.headers
        assert "X-RateLimit-Remaining" in resp.headers
        assert "X-RateLimit-Reset" in resp.headers

    def test_rate_limit_remaining_decreases(self, http_client, user_token):
        headers = {"Authorization": f"Bearer {user_token}"}
        resp1 = http_client.get("/api/v1/doctors", headers=headers)
        remaining1 = int(resp1.headers.get("X-RateLimit-Remaining", 0))

        resp2 = http_client.get("/api/v1/doctors", headers=headers)
        remaining2 = int(resp2.headers.get("X-RateLimit-Remaining", 0))

        assert remaining2 < remaining1

    def test_public_endpoints_not_rate_limited(self, http_client):
        for _ in range(5):
            resp = http_client.post(
                "/api/v1/auth/login",
                json={"username": "nonexistent", "password": "test"},
            )
            assert resp.status_code == 401
            assert "X-RateLimit-Limit" not in resp.headers

    def test_rate_limit_exceeded_returns_429(self, http_client):
        # The default rate limit is 100 requests per 60s window.
        # Send 101 requests to trigger the limit.
        RATE_LIMIT = 100

        username = f"ratelimit_user_{uuid.uuid4().hex[:8]}"
        register = http_client.post(
            "/api/v1/auth/register",
            json={"username": username, "password": "test1234"},
        )
        assert register.status_code == 200
        token = register.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        for i in range(RATE_LIMIT):
            resp = http_client.get("/api/v1/doctors", headers=headers)
            remaining = int(resp.headers.get("X-RateLimit-Remaining", 0))
            assert resp.status_code == 200, (
                f"Failed at request {i}, remaining={remaining}"
            )

        resp = http_client.get("/api/v1/doctors", headers=headers)
        assert resp.status_code == 429
        assert "rate limit" in resp.json()["detail"].lower()
        assert "Retry-After" in resp.headers

    def test_rate_limit_per_user_independent(self, http_client):
        user1 = f"ratelimit_u1_{uuid.uuid4().hex[:8]}"
        user2 = f"ratelimit_u2_{uuid.uuid4().hex[:8]}"

        r1 = http_client.post(
            "/api/v1/auth/register",
            json={"username": user1, "password": "test1234"},
        )
        r2 = http_client.post(
            "/api/v1/auth/register",
            json={"username": user2, "password": "test1234"},
        )
        assert r1.status_code == 200
        assert r2.status_code == 200
        t1 = r1.json()["access_token"]
        t2 = r2.json()["access_token"]

        for _ in range(5):
            http_client.get(
                "/api/v1/doctors",
                headers={"Authorization": f"Bearer {t1}"},
            )

        resp2 = http_client.get(
            "/api/v1/doctors",
            headers={"Authorization": f"Bearer {t2}"},
        )
        assert resp2.status_code == 200
        remaining2 = int(resp2.headers.get("X-RateLimit-Remaining", 0))
        assert remaining2 >= 90
