from starlette.middleware.base import BaseHTTPMiddleware


class DeprecationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/v1/"):
            response.headers["Deprecation"] = "true"
            response.headers["Sunset"] = "2027-01-01T00:00:00Z"
            response.headers["Link"] = '</api/v2/>; rel="successor-version"'
        return response
