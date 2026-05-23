from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from app.db.session import set_rls_context


class TenantMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        tenant_id = request.headers.get("X-Tenant-ID")
        if tenant_id:
            request.state.tenant_id = int(tenant_id)
            set_rls_context(tenant_id=int(tenant_id))
        else:
            request.state.tenant_id = None
        response = await call_next(request)
        return response
