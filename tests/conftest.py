import pytest
import httpx
import asyncio
import os
from datetime import datetime, timedelta, timezone

BASE_URL = os.getenv("BASE_URL", "http://localhost")


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def http_client():
    with httpx.Client(base_url=BASE_URL, timeout=30.0) as client:
        yield client


@pytest.fixture(scope="session")
def admin_token(http_client):
    import uuid

    username = f"admin_test_{uuid.uuid4().hex[:8]}"
    resp = http_client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": "test1234", "role": "admin"},
    )
    assert resp.status_code == 200
    return resp.json()["access_token"]


@pytest.fixture(scope="session")
def superadmin_token(http_client):
    import uuid

    username = f"superadmin_test_{uuid.uuid4().hex[:8]}"
    resp = http_client.post(
        "/api/v1/auth/register",
        json={
            "username": username,
            "password": "test1234",
            "role": "superadmin",
        },
    )
    assert resp.status_code == 200
    return resp.json()["access_token"]


@pytest.fixture(scope="session")
def user_token(http_client):
    import uuid

    username = f"user_test_{uuid.uuid4().hex[:8]}"
    resp = http_client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": "test1234"},
    )
    assert resp.status_code == 200
    return resp.json()["access_token"]


@pytest.fixture
def auth_headers(user_token):
    return {"Authorization": f"Bearer {user_token}"}


@pytest.fixture
def admin_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture(scope="session")
def patient_id(admin_token):
    import uuid

    name = f"Test Patient {uuid.uuid4().hex[:8]}"
    email = f"{uuid.uuid4().hex[:8]}@test.com"
    resp = httpx.Client(base_url=BASE_URL, timeout=10.0).post(
        "/api/v1/patients",
        json={"name": name, "email": email, "phone": "1234567890"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code in (200, 201)
    return resp.json()["id"]


@pytest.fixture(scope="session")
def seeded_doctor_id(user_token):
    return 1


@pytest.fixture
def future_time_slot():
    import uuid

    unique_days = int(uuid.uuid4().hex[:4], 16) % 300
    future = datetime.now(timezone.utc) + timedelta(days=50 + unique_days, hours=10)
    return future.strftime("%Y-%m-%dT%H:%M:%SZ")
