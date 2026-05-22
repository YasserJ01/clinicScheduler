import logging
import json
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import AuditLog

logger = logging.getLogger("clinic.audit")


async def audit_log(
    session: AsyncSession,
    actor: str,
    action: str,
    entity_type: str | None = None,
    entity_id: int | None = None,
    details: dict | None = None,
    outcome: str = "success",
    tenant_id: int | None = None,
) -> None:
    """Append a structured audit entry to the audit_log table and stdout."""
    details_json = json.dumps(details, default=str) if details else None

    entry = AuditLog(
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=details_json,
        outcome=outcome,
        tenant_id=tenant_id,
    )
    session.add(entry)

    log_msg = (
        "AUDIT: action=%s actor=%s entity_type=%s entity_id=%s outcome=%s",
        action,
        actor,
        entity_type,
        entity_id,
        outcome,
    )
    if outcome == "success":
        logger.info(*log_msg)
    else:
        logger.warning(*log_msg)
