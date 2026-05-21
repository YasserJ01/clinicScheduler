import logging

import redis

from app.config import settings

logger = logging.getLogger("clinic.metrics")


class MetricsCollector:
    """Redis-backed Prometheus metrics collector.

    Uses Redis hashes and counters for persistent, cross-worker metrics.
    """

    def __init__(self, redis_url: str = settings.REDIS_URL):
        self.redis = redis.from_url(redis_url, decode_responses=True)
        self._prefix = "clinic_metrics"

    def _key(self, *parts: str) -> str:
        return f"{self._prefix}:{':'.join(parts)}"

    def increment_request(self, method: str, endpoint: str, status: int) -> None:
        """Increment HTTP request counter."""
        key = self._key("http_requests_total", method, endpoint, str(status))
        self.redis.incr(key)

    def observe_duration(self, method: str, endpoint: str, duration: float) -> None:
        """Record request duration for histogram approximation."""
        key = self._key("http_request_duration_sum", method, endpoint)
        self.redis.incrbyfloat(key, duration)

        count_key = self._key("http_request_duration_count", method, endpoint)
        self.redis.incr(count_key)

        bucket = self._duration_bucket(duration)
        bucket_key = self._key(
            "http_request_duration_bucket", method, endpoint, str(bucket)
        )
        self.redis.incr(bucket_key)

    def _duration_bucket(self, duration: float) -> str:
        """Return the bucket label for a given duration (in seconds)."""
        thresholds = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
        for t in thresholds:
            if duration <= t:
                return f"le={t}"
        return "le=+Inf"

    def increment_booking(self, status: str) -> None:
        """Increment booking counter."""
        key = self._key("appointment_bookings_total", status)
        self.redis.incr(key)

    def set_circuit_breaker_state(self, name: str, state: int) -> None:
        """Set circuit breaker state gauge (0=CLOSED, 1=OPEN, 2=HALF_OPEN)."""
        key = self._key("circuit_breaker_state", name)
        self.redis.set(key, str(state))

    def get_all_metrics(self) -> str:
        """Return all metrics in Prometheus exposition format."""
        lines = []
        lines.append("# HELP http_requests_total Total number of HTTP requests")
        lines.append("# TYPE http_requests_total counter")
        for key in self.redis.keys(self._key("http_requests_total", "*")):
            parts = key.split(":")
            if len(parts) < 5:
                continue
            method = parts[2]
            endpoint = parts[3]
            status = parts[4]
            value = self.redis.get(key) or "0"
            lines.append(
                f'http_requests_total{{method="{method}",endpoint="{endpoint}",status="{status}"}} {value}'
            )

        lines.append("")
        lines.append(
            "# HELP http_request_duration_seconds HTTP request duration in seconds"
        )
        lines.append("# TYPE http_request_duration_seconds histogram")
        for key in self.redis.keys(self._key("http_request_duration_bucket", "*")):
            parts = key.split(":")
            if len(parts) < 5:
                continue
            method = parts[2]
            endpoint = parts[3]
            bucket = parts[4]
            value = self.redis.get(key) or "0"
            lines.append(
                f'http_request_duration_seconds_bucket{{method="{method}",endpoint="{endpoint}",{bucket}}} {value}'
            )

        for key in self.redis.keys(self._key("http_request_duration_sum", "*")):
            parts = key.split(":")
            if len(parts) < 4:
                continue
            method = parts[2]
            endpoint = parts[3]
            value = self.redis.get(key) or "0"
            lines.append(
                f'http_request_duration_seconds_sum{{method="{method}",endpoint="{endpoint}"}} {value}'
            )

        for key in self.redis.keys(self._key("http_request_duration_count", "*")):
            parts = key.split(":")
            if len(parts) < 4:
                continue
            method = parts[2]
            endpoint = parts[3]
            value = self.redis.get(key) or "0"
            lines.append(
                f'http_request_duration_seconds_count{{method="{method}",endpoint="{endpoint}"}} {value}'
            )

        lines.append("")
        lines.append(
            "# HELP appointment_bookings_total Total number of appointment bookings"
        )
        lines.append("# TYPE appointment_bookings_total counter")
        for key in self.redis.keys(self._key("appointment_bookings_total", "*")):
            parts = key.split(":")
            if len(parts) < 4:
                continue
            status = parts[3]
            value = self.redis.get(key) or "0"
            lines.append(f'appointment_bookings_total{{status="{status}"}} {value}')

        lines.append("")
        lines.append(
            "# HELP circuit_breaker_state Circuit breaker state (0=CLOSED, 1=OPEN, 2=HALF_OPEN)"
        )
        lines.append("# TYPE circuit_breaker_state gauge")
        for key in self.redis.keys(self._key("circuit_breaker_state", "*")):
            parts = key.split(":")
            if len(parts) < 4:
                continue
            name = parts[3]
            value = self.redis.get(key) or "0"
            lines.append(f'circuit_breaker_state{{name="{name}"}} {value}')

        return "\n".join(lines)


metrics_collector = MetricsCollector()
