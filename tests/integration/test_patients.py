import uuid


class TestListPatients:
    def test_list_patients_authenticated(self, http_client, auth_headers):
        resp = http_client.get("/api/v1/patients", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    def test_list_patients_unauthenticated(self, http_client):
        resp = http_client.get("/api/v1/patients")
        assert resp.status_code in (401, 403)


class TestPatientProfile:
    def test_get_my_profile_authenticated(self, http_client, user_token):
        resp = http_client.get(
            "/api/v1/patients/me",
            headers={"Authorization": f"Bearer {user_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "id" in data
        assert "name" in data
        assert "email" in data

    def test_get_my_profile_unauthenticated(self, http_client):
        resp = http_client.get("/api/v1/patients/me")
        assert resp.status_code in (401, 403)

    def test_get_my_profile_returns_username(self, http_client):
        username = f"profile_test_{uuid.uuid4().hex[:8]}"
        resp = http_client.post(
            "/api/v1/auth/register",
            json={"username": username, "password": "test1234"},
        )
        token = resp.json()["access_token"]
        resp = http_client.get(
            "/api/v1/patients/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == username
