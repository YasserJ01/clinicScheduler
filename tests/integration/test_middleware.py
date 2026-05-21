import msgpack


class TestMessagePackMiddleware:
    def test_response_time_header_present(self, http_client, auth_headers):
        resp = http_client.get("/api/v1/doctors", headers=auth_headers)
        assert resp.status_code == 200
        assert "X-Response-Time" in resp.headers
        assert resp.headers["X-Response-Time"].endswith("ms")

    def test_response_time_is_positive(self, http_client, auth_headers):
        resp = http_client.get("/api/v1/doctors", headers=auth_headers)
        time_str = resp.headers["X-Response-Time"]
        value = float(time_str.replace("ms", ""))
        assert value >= 0

    def test_msgpack_accept_returns_binary(self, http_client, auth_headers):
        resp = http_client.get(
            "/api/v1/doctors",
            headers={**auth_headers, "Accept": "application/x-msgpack"},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/x-msgpack"
        data = msgpack.unpackb(resp.content, raw=False)
        assert isinstance(data, dict)
        assert "items" in data
        assert isinstance(data["items"], list)
        assert len(data["items"]) >= 2

    def test_msgpack_response_matches_json_response(self, http_client, auth_headers):
        json_resp = http_client.get("/api/v1/doctors", headers=auth_headers)
        msgpack_resp = http_client.get(
            "/api/v1/doctors",
            headers={**auth_headers, "Accept": "application/x-msgpack"},
        )

        json_data = json_resp.json()
        msgpack_data = msgpack.unpackb(msgpack_resp.content, raw=False)
        assert json_data == msgpack_data

    def test_health_check_has_response_time(self, http_client, auth_headers):
        resp = http_client.get("/api/v1/health", headers=auth_headers)
        assert resp.status_code == 200
        assert "X-Response-Time" in resp.headers

    def test_msgpack_response_has_content_length(self, http_client, auth_headers):
        resp = http_client.get(
            "/api/v1/doctors",
            headers={**auth_headers, "Accept": "application/x-msgpack"},
        )
        assert "content-length" in resp.headers
        assert int(resp.headers["content-length"]) == len(resp.content)
