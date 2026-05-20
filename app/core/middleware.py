import time
import msgpack
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware


class MessagePackMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        accept = request.headers.get("accept", "")
        content_type = request.headers.get("content-type", "")

        if "application/x-msgpack" in content_type and request.method in ("POST", "PUT", "PATCH"):
            body = await request.body()
            try:
                request._msgpack_data = msgpack.unpackb(body, raw=False)
            except Exception:
                return Response(content="Invalid MessagePack payload", status_code=400)

        start_time = time.monotonic()
        response = await call_next(request)
        elapsed = time.monotonic() - start_time

        response.headers["X-Response-Time"] = f"{elapsed*1000:.2f}ms"

        if "application/x-msgpack" in accept:
            body_bytes = b""
            async for chunk in response.body_iterator:
                body_bytes += chunk
            try:
                import json
                data = json.loads(body_bytes)
                packed = msgpack.packb(data, use_bin_type=True)
                return Response(
                    content=packed,
                    status_code=response.status_code,
                    headers={
                        **response.headers,
                        "content-type": "application/x-msgpack",
                        "content-length": str(len(packed)),
                    },
                )
            except Exception:
                pass

        return response
