import time
import re
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.metrics import metrics_collector


class MetricsMiddleware(BaseHTTPMiddleware):
    """Middleware to track HTTP request metrics for Prometheus."""

    async def dispatch(self, request: Request, call_next) -> Response:
        start_time = time.time()

        response = await call_next(request)

        duration = time.time() - start_time

        endpoint = self._normalise_endpoint(request.url.path, request.method)
        metrics_collector.increment_request(
            request.method, endpoint, response.status_code
        )
        metrics_collector.observe_duration(request.method, endpoint, duration)

        if request.method == "POST" and request.url.path.startswith(
            "/api/v1/appointments"
        ):
            if response.status_code == 201:
                metrics_collector.increment_booking("success")
            elif response.status_code == 409:
                metrics_collector.increment_booking("conflict")
            else:
                metrics_collector.increment_booking("failed")

        return response

    @staticmethod
    def _normalise_endpoint(path: str, method: str) -> str:
        """Normalise endpoint path for metrics (replace IDs with {id})."""
        if method in ("GET", "DELETE", "PUT", "PATCH"):
            path = re.sub(r"/\d+", "/{id}", path)
        return path
