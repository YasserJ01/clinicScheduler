import pytest
import httpx
import asyncio

BASE_URL = "http://localhost"


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def http_client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as client:
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
