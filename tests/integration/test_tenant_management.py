import uuid


class TestTenantManagement:
    def test_list_tenants_superadmin(self, http_client, superadmin_token):
        resp = http_client.get(
            "/api/v1/admin/tenants",
            headers={"Authorization": f"Bearer {superadmin_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert data["total"] >= 1

    def test_list_tenants_requires_superadmin(self, http_client, admin_token):
        resp = http_client.get(
            "/api/v1/admin/tenants",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 403

    def test_list_tenants_requires_auth(self, http_client):
        resp = http_client.get("/api/v1/admin/tenants")
        assert resp.status_code in (401, 403)

    def test_create_tenant(self, http_client, superadmin_token):
        slug = f"test-tenant-{uuid.uuid4().hex[:8]}"
        resp = http_client.post(
            "/api/v1/admin/tenants",
            json={"name": "Test Tenant", "slug": slug},
            headers={"Authorization": f"Bearer {superadmin_token}"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Test Tenant"
        assert data["slug"] == slug
        assert data["is_active"] is True

    def test_create_tenant_duplicate_slug(self, http_client, superadmin_token):
        slug = f"dup-slug-{uuid.uuid4().hex[:8]}"
        resp = http_client.post(
            "/api/v1/admin/tenants",
            json={"name": "First", "slug": slug},
            headers={"Authorization": f"Bearer {superadmin_token}"},
        )
        assert resp.status_code == 201

        resp = http_client.post(
            "/api/v1/admin/tenants",
            json={"name": "Second", "slug": slug},
            headers={"Authorization": f"Bearer {superadmin_token}"},
        )
        assert resp.status_code == 409

    def test_create_tenant_requires_superadmin(self, http_client, admin_token):
        resp = http_client.post(
            "/api/v1/admin/tenants",
            json={"name": "Test", "slug": "test-tenant"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 403

    def test_get_tenant(self, http_client, superadmin_token):
        resp = http_client.get(
            "/api/v1/admin/tenants/1",
            headers={"Authorization": f"Bearer {superadmin_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == 1
        assert data["slug"] == "default"

    def test_get_tenant_not_found(self, http_client, superadmin_token):
        resp = http_client.get(
            "/api/v1/admin/tenants/99999",
            headers={"Authorization": f"Bearer {superadmin_token}"},
        )
        assert resp.status_code == 404

    def test_update_tenant(self, http_client, superadmin_token):
        slug = f"update-tenant-{uuid.uuid4().hex[:8]}"
        create = http_client.post(
            "/api/v1/admin/tenants",
            json={"name": "Original", "slug": slug},
            headers={"Authorization": f"Bearer {superadmin_token}"},
        )
        assert create.status_code == 201
        tenant_id = create.json()["id"]

        resp = http_client.patch(
            f"/api/v1/admin/tenants/{tenant_id}",
            json={"name": "Updated Name"},
            headers={"Authorization": f"Bearer {superadmin_token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Updated Name"

    def test_deactivate_tenant(self, http_client, superadmin_token):
        slug = f"deact-tenant-{uuid.uuid4().hex[:8]}"
        create = http_client.post(
            "/api/v1/admin/tenants",
            json={"name": "To Deactivate", "slug": slug},
            headers={"Authorization": f"Bearer {superadmin_token}"},
        )
        assert create.status_code == 201
        tenant_id = create.json()["id"]

        resp = http_client.delete(
            f"/api/v1/admin/tenants/{tenant_id}",
            headers={"Authorization": f"Bearer {superadmin_token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        get_resp = http_client.get(
            f"/api/v1/admin/tenants/{tenant_id}",
            headers={"Authorization": f"Bearer {superadmin_token}"},
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["is_active"] is False

    def test_cannot_deactivate_default_tenant(self, http_client, superadmin_token):
        resp = http_client.delete(
            "/api/v1/admin/tenants/1",
            headers={"Authorization": f"Bearer {superadmin_token}"},
        )
        assert resp.status_code == 400
        assert "default tenant" in resp.json()["detail"].lower()

    def test_superadmin_can_access_admin_endpoints(self, http_client, superadmin_token):
        resp = http_client.get(
            "/api/v1/admin/webhooks",
            headers={"Authorization": f"Bearer {superadmin_token}"},
        )
        assert resp.status_code == 200
