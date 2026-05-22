from datetime import datetime, timedelta


class TestWebhookCRUD:
    def test_create_webhook(self, http_client, admin_headers):
        resp = http_client.post(
            "/api/v1/admin/webhooks",
            json={
                "url": "https://example.com/webhook",
                "events": ["appointment.created", "appointment.cancelled"],
                "is_active": True,
            },
            headers=admin_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["url"] == "https://example.com/webhook"
        assert data["events"] == ["appointment.created", "appointment.cancelled"]
        assert "secret" in data
        return data["id"]

    def test_list_webhooks(self, http_client, admin_headers):
        http_client.post(
            "/api/v1/admin/webhooks",
            json={
                "url": "https://example.com/webhook-list",
                "events": ["appointment.created"],
            },
            headers=admin_headers,
        )
        resp = http_client.get("/api/v1/admin/webhooks", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data

    def test_get_webhook(self, http_client, admin_headers):
        create_resp = http_client.post(
            "/api/v1/admin/webhooks",
            json={
                "url": "https://example.com/webhook-get",
                "events": ["appointment.created"],
            },
            headers=admin_headers,
        )
        webhook_id = create_resp.json()["id"]
        resp = http_client.get(
            f"/api/v1/admin/webhooks/{webhook_id}",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == webhook_id

    def test_update_webhook(self, http_client, admin_headers):
        create_resp = http_client.post(
            "/api/v1/admin/webhooks",
            json={
                "url": "https://example.com/webhook-update",
                "events": ["appointment.created"],
            },
            headers=admin_headers,
        )
        webhook_id = create_resp.json()["id"]
        resp = http_client.patch(
            f"/api/v1/admin/webhooks/{webhook_id}",
            json={"is_active": False},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_active"] is False

    def test_delete_webhook(self, http_client, admin_headers):
        create_resp = http_client.post(
            "/api/v1/admin/webhooks",
            json={
                "url": "https://example.com/webhook-delete",
                "events": ["appointment.created"],
            },
            headers=admin_headers,
        )
        webhook_id = create_resp.json()["id"]
        resp = http_client.delete(
            f"/api/v1/admin/webhooks/{webhook_id}",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        resp = http_client.get(
            f"/api/v1/admin/webhooks/{webhook_id}",
            headers=admin_headers,
        )
        assert resp.status_code == 404

    def test_list_webhook_deliveries(self, http_client, admin_headers):
        create_resp = http_client.post(
            "/api/v1/admin/webhooks",
            json={
                "url": "https://example.com/webhook-deliveries",
                "events": ["appointment.created"],
            },
            headers=admin_headers,
        )
        webhook_id = create_resp.json()["id"]
        resp = http_client.get(
            f"/api/v1/admin/webhooks/{webhook_id}/deliveries",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data

    def test_webhook_requires_admin(self, http_client, auth_headers):
        resp = http_client.post(
            "/api/v1/admin/webhooks",
            json={
                "url": "https://example.com/webhook",
                "events": ["appointment.created"],
            },
            headers=auth_headers,
        )
        assert resp.status_code == 403


class TestAnalyticsEndpoints:
    def test_analytics_summary(self, http_client, admin_headers):
        resp = http_client.get(
            "/api/v1/admin/analytics/summary",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "total_appointments" in data
        assert "total_patients" in data
        assert "total_doctors" in data
        assert "cancelled_appointments" in data
        assert "cancellation_rate" in data

    def test_analytics_summary_with_dates(self, http_client, admin_headers):
        from_date = (datetime.utcnow() - timedelta(days=30)).isoformat() + "Z"
        to_date = datetime.utcnow().isoformat() + "Z"
        resp = http_client.get(
            f"/api/v1/admin/analytics/summary?from_date={from_date}&to_date={to_date}",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["period"]["from"] == from_date
        assert data["period"]["to"] == to_date

    def test_analytics_requires_admin(self, http_client, auth_headers):
        resp = http_client.get(
            "/api/v1/admin/analytics/summary",
            headers=auth_headers,
        )
        assert resp.status_code == 403


class TestDoctorMobileAPI:
    def test_get_doctor_patients(self, http_client, admin_headers):
        resp = http_client.get(
            "/api/v1/doctors/1/patients",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "patients" in data

    def test_get_today_appointments(self, http_client, admin_headers):
        resp = http_client.get(
            "/api/v1/doctors/1/appointments/today",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "appointments" in data
        assert "date" in data

    def test_get_upcoming_appointments(self, http_client, admin_headers):
        resp = http_client.get(
            "/api/v1/doctors/1/appointments/upcoming?days=14",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "appointments" in data
        assert data["days"] == 14

    def test_doctor_mobile_requires_doctor_role(self, http_client, auth_headers):
        resp = http_client.get(
            "/api/v1/doctors/1/appointments/today",
            headers=auth_headers,
        )
        assert resp.status_code == 403


class TestAdminPatientData:
    def test_export_patient_data(self, http_client, admin_headers, patient_id):
        resp = http_client.get(
            f"/api/v1/admin/patients/{patient_id}/export",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert "application/x-ndjson" in resp.headers.get("content-type", "")

    def test_anonymise_patient(self, http_client, admin_headers):
        import uuid

        name = f"Test Anon Patient {uuid.uuid4().hex[:8]}"
        email = f"{uuid.uuid4().hex[:8]}@anon-test.com"
        create_resp = http_client.post(
            "/api/v1/patients",
            json={"name": name, "email": email},
            headers=admin_headers,
        )
        assert create_resp.status_code in (200, 201)
        patient_id = create_resp.json()["id"]

        resp = http_client.delete(
            f"/api/v1/admin/patients/{patient_id}",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "ANONYMIZED" in data["patient"]["name"]

    def test_admin_requires_admin_role(self, http_client, auth_headers, patient_id):
        resp = http_client.delete(
            f"/api/v1/admin/patients/{patient_id}",
            headers=auth_headers,
        )
        assert resp.status_code == 403
