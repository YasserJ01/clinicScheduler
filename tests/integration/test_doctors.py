class TestListDoctors:
    def test_list_doctors_authenticated(self, http_client, auth_headers):
        resp = http_client.get("/api/v1/doctors", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert isinstance(data["items"], list)
        assert len(data["items"]) >= 2

    def test_list_doctors_unauthenticated(self, http_client):
        resp = http_client.get("/api/v1/doctors")
        assert resp.status_code in (401, 403)

    def test_list_doctors_returns_expected_fields(self, http_client, auth_headers):
        resp = http_client.get("/api/v1/doctors", headers=auth_headers)
        data = resp.json()
        for doctor in data["items"]:
            assert "id" in doctor
            assert "name" in doctor
            assert "specialty" in doctor

    def test_list_doctors_pagination_returns_envelope(self, http_client, auth_headers):
        resp = http_client.get("/api/v1/doctors", headers=auth_headers)
        data = resp.json()
        assert "total" in data
        assert "page" in data
        assert "page_size" in data
        assert "pages" in data
        assert data["page"] == 1
        assert data["page_size"] == 20


class TestCreateDoctor:
    def test_create_doctor_as_admin(self, http_client, admin_headers):
        resp = http_client.post(
            "/api/v1/doctors",
            json={"name": "Dr. Test", "specialty": "Testing"},
            headers=admin_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Dr. Test"
        assert data["specialty"] == "Testing"
        assert "id" in data

    def test_create_doctor_as_non_admin(self, http_client, auth_headers):
        resp = http_client.post(
            "/api/v1/doctors",
            json={"name": "Dr. Unauthorized", "specialty": "Testing"},
            headers=auth_headers,
        )
        assert resp.status_code == 403
        assert "Admin access required" in resp.json()["detail"]

    def test_create_doctor_unauthenticated(self, http_client):
        resp = http_client.post(
            "/api/v1/doctors",
            json={"name": "Dr. No Auth", "specialty": "Testing"},
        )
        assert resp.status_code in (401, 403)
