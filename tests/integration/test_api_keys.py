class TestApiKeyManagement:
    def test_create_api_key(self, http_client, admin_token):
        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = http_client.post(
            "/api/v1/admin/api-keys",
            json={"name": "Test Key", "role": "patient"},
            headers=headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Test Key"
        assert data["role"] == "patient"
        assert len(data["raw_key"]) == 64
        assert data["key_prefix"] == data["raw_key"][:8]
        assert data["is_active"] is True

    def test_create_api_key_with_expiry(self, http_client, admin_token):
        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = http_client.post(
            "/api/v1/admin/api-keys",
            json={
                "name": "Expiring Key",
                "role": "admin",
                "expires_at": "2027-12-31T23:59:59",
            },
            headers=headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["role"] == "admin"
        assert data["expires_at"] is not None

    def test_list_api_keys(self, http_client, admin_token):
        headers = {"Authorization": f"Bearer {admin_token}"}
        http_client.post(
            "/api/v1/admin/api-keys",
            json={"name": "List Test Key"},
            headers=headers,
        )
        resp = http_client.get("/api/v1/admin/api-keys", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        assert len(data["items"]) >= 1
        assert "key_prefix" in data["items"][0]
        assert "raw_key" not in data["items"][0]

    def test_api_key_requires_admin(self, http_client, user_token):
        headers = {"Authorization": f"Bearer {user_token}"}
        resp = http_client.post(
            "/api/v1/admin/api-keys",
            json={"name": "Unauthorized Key"},
            headers=headers,
        )
        assert resp.status_code == 403

    def test_deactivate_api_key(self, http_client, admin_token):
        headers = {"Authorization": f"Bearer {admin_token}"}
        create = http_client.post(
            "/api/v1/admin/api-keys",
            json={"name": "To Deactivate"},
            headers=headers,
        )
        assert create.status_code == 201
        key_id = create.json()["id"]

        resp = http_client.patch(
            f"/api/v1/admin/api-keys/{key_id}",
            json={"is_active": False},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["is_active"] is False

    def test_delete_api_key(self, http_client, admin_token):
        headers = {"Authorization": f"Bearer {admin_token}"}
        create = http_client.post(
            "/api/v1/admin/api-keys",
            json={"name": "To Delete"},
            headers=headers,
        )
        assert create.status_code == 201
        key_id = create.json()["id"]

        resp = http_client.delete(
            f"/api/v1/admin/api-keys/{key_id}",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True


class TestApiKeyAuthentication:
    def test_authenticate_with_api_key(self, http_client, admin_token):
        headers = {"Authorization": f"Bearer {admin_token}"}
        create = http_client.post(
            "/api/v1/admin/api-keys",
            json={"name": "Auth Test Key", "role": "patient"},
            headers=headers,
        )
        assert create.status_code == 201
        raw_key = create.json()["raw_key"]

        resp = http_client.get(
            "/api/v1/doctors",
            headers={"X-API-Key": raw_key},
        )
        assert resp.status_code == 200

    def test_invalid_api_key_rejected(self, http_client):
        resp = http_client.get(
            "/api/v1/doctors",
            headers={"X-API-Key": "invalid_key_here"},
        )
        assert resp.status_code == 401

    def test_deactivated_api_key_rejected(self, http_client, admin_token):
        headers = {"Authorization": f"Bearer {admin_token}"}
        create = http_client.post(
            "/api/v1/admin/api-keys",
            json={"name": "Deactivated Auth Key"},
            headers=headers,
        )
        assert create.status_code == 201
        key_id = create.json()["id"]
        raw_key = create.json()["raw_key"]

        http_client.patch(
            f"/api/v1/admin/api-keys/{key_id}",
            json={"is_active": False},
            headers=headers,
        )

        resp = http_client.get(
            "/api/v1/doctors",
            headers={"X-API-Key": raw_key},
        )
        assert resp.status_code == 401

    def test_no_auth_rejected(self, http_client):
        resp = http_client.get("/api/v1/doctors")
        assert resp.status_code in (401, 403)
