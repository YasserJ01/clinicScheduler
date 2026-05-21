from datetime import datetime, timedelta, timezone


class TestBookAppointment:
    def test_book_appointment_success(
        self, http_client, auth_headers, patient_id, seeded_doctor_id, future_time_slot
    ):
        resp = http_client.post(
            "/api/v1/appointments",
            headers=auth_headers,
            json={
                "doctor_id": seeded_doctor_id,
                "patient_id": patient_id,
                "time_slot": future_time_slot,
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["success"] is True
        assert data["node_id"] is not None
        assert data["error"] is None
        assert data["appointment"] is not None
        assert data["appointment"]["doctor_id"] == seeded_doctor_id
        assert data["appointment"]["patient_id"] == patient_id

    def test_book_appointment_conflict(
        self, http_client, auth_headers, patient_id, seeded_doctor_id
    ):
        time_slot = "2027-01-15T14:00:00Z"

        first = http_client.post(
            "/api/v1/appointments",
            headers=auth_headers,
            json={
                "doctor_id": seeded_doctor_id,
                "patient_id": patient_id,
                "time_slot": time_slot,
            },
        )
        assert first.status_code in (201, 409)

        resp = http_client.post(
            "/api/v1/appointments",
            headers=auth_headers,
            json={
                "doctor_id": seeded_doctor_id,
                "patient_id": patient_id,
                "time_slot": time_slot,
            },
        )
        assert resp.status_code == 409
        data = resp.json()
        assert data["success"] is False
        assert "already occupied" in data["error"]

    def test_book_appointment_invalid_doctor(
        self, http_client, auth_headers, patient_id, future_time_slot
    ):
        resp = http_client.post(
            "/api/v1/appointments",
            headers=auth_headers,
            json={
                "doctor_id": 99999,
                "patient_id": patient_id,
                "time_slot": future_time_slot,
            },
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["success"] is False
        assert "Doctor not found" in data["error"]

    def test_book_appointment_invalid_patient(
        self, http_client, auth_headers, seeded_doctor_id
    ):
        resp = http_client.post(
            "/api/v1/appointments",
            headers=auth_headers,
            json={
                "doctor_id": seeded_doctor_id,
                "patient_id": 99999,
                "time_slot": "2027-06-15T16:00:00Z",
            },
        )
        assert resp.status_code == 404
        data = resp.json()
        assert data["success"] is False
        assert "Patient with id 99999 not found" in data["error"]

    def test_book_appointment_malformed_timeslot(
        self, http_client, auth_headers, patient_id, seeded_doctor_id
    ):
        resp = http_client.post(
            "/api/v1/appointments",
            headers=auth_headers,
            json={
                "doctor_id": seeded_doctor_id,
                "patient_id": patient_id,
                "time_slot": "not-a-date",
            },
        )
        assert resp.status_code == 422

    def test_book_appointment_chaos_trigger(
        self, http_client, auth_headers, seeded_doctor_id
    ):
        resp = http_client.post(
            "/api/v1/appointments",
            headers=auth_headers,
            json={
                "doctor_id": seeded_doctor_id,
                "patient_id": 999,
                "time_slot": "2027-01-15T15:00:00Z",
            },
        )
        assert resp.status_code == 503
        data = resp.json()
        assert "CHAOS" in data["detail"]

    def test_book_appointment_unauthenticated(
        self, http_client, patient_id, seeded_doctor_id, future_time_slot
    ):
        resp = http_client.post(
            "/api/v1/appointments",
            json={
                "doctor_id": seeded_doctor_id,
                "patient_id": patient_id,
                "time_slot": future_time_slot,
            },
        )
        assert resp.status_code in (401, 403)

    def test_node_id_reflects_hostname(
        self, http_client, auth_headers, patient_id, seeded_doctor_id
    ):
        import uuid
        from datetime import datetime, timedelta, timezone

        uid = uuid.uuid4().hex[:8]
        hour = int(uid[:2], 16) % 24
        minute = int(uid[2:4], 16) % 60
        second = int(uid[4:6], 16) % 60
        day_offset = int(uid[6:8], 16) % 200
        future_date = datetime.now(timezone.utc) + timedelta(days=200 + day_offset)
        unique_slot = future_date.strftime(
            f"%Y-%m-%dT{hour:02d}:{minute:02d}:{second:02d}Z"
        )
        resp = http_client.post(
            "/api/v1/appointments",
            headers=auth_headers,
            json={
                "doctor_id": seeded_doctor_id,
                "patient_id": patient_id,
                "time_slot": unique_slot,
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["node_id"] is not None
        assert len(data["node_id"]) > 0


class TestListAppointments:
    def test_list_appointments(self, http_client, auth_headers):
        resp = http_client.get("/api/v1/appointments", headers=auth_headers)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_list_appointments_unauthenticated(self, http_client):
        resp = http_client.get("/api/v1/appointments")
        assert resp.status_code in (401, 403)

    def test_list_appointments_ordered_by_time(
        self, http_client, auth_headers, patient_id, seeded_doctor_id
    ):
        resp = http_client.get("/api/v1/appointments", headers=auth_headers)
        assert resp.status_code == 200
        appointments = resp.json()
        if len(appointments) >= 2:
            times = [a["time_slot"] for a in appointments]
            assert times == sorted(times)


class TestGetAppointment:
    def test_get_appointment_by_id(
        self, http_client, auth_headers, patient_id, seeded_doctor_id
    ):
        import uuid

        uid = uuid.uuid4().hex[:8]
        hour = int(uid[:2], 16) % 24
        minute = int(uid[2:4], 16) % 60
        second = int(uid[4:6], 16) % 60
        day_offset = int(uid[6:8], 16) % 200
        future_date = datetime.now(timezone.utc) + timedelta(days=250 + day_offset)
        unique_slot = future_date.strftime(
            f"%Y-%m-%dT{hour:02d}:{minute:02d}:{second:02d}Z"
        )
        create_resp = http_client.post(
            "/api/v1/appointments",
            headers=auth_headers,
            json={
                "doctor_id": seeded_doctor_id,
                "patient_id": patient_id,
                "time_slot": unique_slot,
            },
        )
        assert create_resp.status_code == 201
        appt_id = create_resp.json()["appointment"]["id"]

        resp = http_client.get(f"/api/v1/appointments/{appt_id}", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == appt_id
        assert data["doctor_id"] == seeded_doctor_id
        assert data["patient_id"] == patient_id

    def test_get_appointment_invalid_id(self, http_client, auth_headers):
        resp = http_client.get("/api/v1/appointments/999999", headers=auth_headers)
        assert resp.status_code == 404

    def test_get_appointment_unauthenticated(self, http_client):
        resp = http_client.get("/api/v1/appointments/1")
        assert resp.status_code in (401, 403)
