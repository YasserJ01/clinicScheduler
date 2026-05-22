import json
import logging
import secrets
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
from app.db.repository import PatientRepository, AppointmentRepository
from app.api.v1.dependencies import get_current_user
from app.core.audit import audit_log
from app.models import Doctor, User, UserRole, Webhook, WebhookDelivery

logger = logging.getLogger("clinic.admin")

router = APIRouter(prefix="/admin", tags=["admin"])


def _require_admin(current_user: dict) -> None:
    if current_user["role"] != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required"
        )


async def _patient_ndjson(patient_id: int, db: AsyncSession):
    """Stream patient data and their appointments as NDJSON."""
    patient_repo = PatientRepository(db)
    patient = await patient_repo.get_by_id(patient_id)
    if not patient:
        return

    yield (
        json.dumps(
            {
                "type": "patient",
                "id": patient.id,
                "name": patient.name,
                "email": patient.email,
                "phone": patient.phone,
                "created_at": patient.created_at.isoformat()
                if patient.created_at
                else None,
            }
        )
        + "\n"
    )

    appt_repo = AppointmentRepository(db)
    all_appointments = await appt_repo.list_all()
    for appt in all_appointments:
        if appt.patient_id == patient_id:
            yield (
                json.dumps(
                    {
                        "type": "appointment",
                        "id": appt.id,
                        "doctor_id": appt.doctor_id,
                        "appointment_time": appt.appointment_time.isoformat()
                        if appt.appointment_time
                        else None,
                        "status": appt.status.value,
                        "notes": appt.notes,
                        "created_at": appt.created_at.isoformat()
                        if appt.created_at
                        else None,
                    }
                )
                + "\n"
            )


@router.get("/patients/{patient_id}/export")
async def export_patient_data(
    patient_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Export a patient's personal data as NDJSON (GDPR Article 20)."""
    _require_admin(current_user)

    patient_repo = PatientRepository(db)
    patient = await patient_repo.get_by_id(patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    return StreamingResponse(
        _patient_ndjson(patient_id, db),
        media_type="application/x-ndjson",
        headers={
            "Content-Disposition": f'attachment; filename="patient_{patient_id}_export.ndjson"'
        },
    )


@router.delete("/patients/{patient_id}")
async def anonymise_patient(
    patient_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Anonymise a patient's personal data (GDPR Article 17 — Right to Erasure).

    Replaces name, email, and phone with anonymised placeholders.
    Preserves referential integrity (appointments are NOT deleted).
    """
    _require_admin(current_user)

    patient_repo = PatientRepository(db)
    patient = await patient_repo.get_by_id(patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    await patient_repo.anonymise(patient_id)

    await audit_log(
        db,
        actor=current_user["user_id"],
        action="anonymise_patient",
        entity_type="patient",
        entity_id=patient_id,
        details={
            "original_name": patient.name,
            "anonymised_name": f"ANONYMIZED-{patient.id}",
        },
    )

    return {
        "success": True,
        "message": f"Patient {patient_id} anonymised",
        "patient": {
            "id": patient.id,
            "name": f"ANONYMIZED-{patient.id}",
            "email": f"anonymized-{patient.id}@redacted.local",
            "phone": None,
        },
    }


class WebhookCreate(BaseModel):
    url: str
    events: list[str]
    is_active: bool = True


class WebhookUpdate(BaseModel):
    url: str | None = None
    events: list[str] | None = None
    is_active: bool | None = None


@router.post("/webhooks", status_code=201)
async def create_webhook(
    req: WebhookCreate,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(current_user)
    tenant_id = current_user.get("tenant_id", 1)

    secret = secrets.token_hex(32)
    webhook = Webhook(
        url=req.url,
        secret=secret,
        events=json.dumps(req.events),
        is_active=req.is_active,
        created_by=current_user["user_id"],
        tenant_id=tenant_id,
    )
    db.add(webhook)
    await db.flush()

    await audit_log(
        db,
        actor=current_user["user_id"],
        action="create_webhook",
        entity_type="webhook",
        entity_id=webhook.id,
        details={"url": req.url, "events": req.events},
    )

    return {
        "id": webhook.id,
        "url": webhook.url,
        "events": req.events,
        "is_active": webhook.is_active,
        "secret": secret,
        "created_at": webhook.created_at.isoformat() if webhook.created_at else None,
    }


@router.get("/webhooks")
async def list_webhooks(
    page: int = 1,
    page_size: int = 20,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(current_user)
    tenant_id = current_user.get("tenant_id")

    where_clauses = []
    if tenant_id is not None:
        where_clauses.append(Webhook.tenant_id == tenant_id)
    count_stmt = select(func.count(Webhook.id))
    if where_clauses:
        count_stmt = count_stmt.where(*where_clauses)
    count_result = await db.execute(count_stmt)
    total = count_result.scalar() or 0
    stmt = (
        select(Webhook)
        .where(*where_clauses)
        .order_by(Webhook.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(stmt)
    webhooks = result.scalars().all()

    items = [
        {
            "id": w.id,
            "url": w.url,
            "events": json.loads(w.events),
            "is_active": w.is_active,
            "created_by": w.created_by,
            "created_at": w.created_at.isoformat() if w.created_at else None,
        }
        for w in webhooks
    ]

    pages = (total + page_size - 1) // page_size if total > 0 else 0
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
    }


@router.get("/webhooks/{webhook_id}")
async def get_webhook(
    webhook_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(current_user)
    tenant_id = current_user.get("tenant_id")

    result = await db.execute(
        select(Webhook).where(Webhook.id == webhook_id, Webhook.tenant_id == tenant_id)
    )
    webhook = result.scalar_one_or_none()
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook not found")

    return {
        "id": webhook.id,
        "url": webhook.url,
        "events": json.loads(webhook.events),
        "is_active": webhook.is_active,
        "created_by": webhook.created_by,
        "created_at": webhook.created_at.isoformat() if webhook.created_at else None,
    }


@router.patch("/webhooks/{webhook_id}")
async def update_webhook(
    webhook_id: int,
    req: WebhookUpdate,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(current_user)
    tenant_id = current_user.get("tenant_id")

    result = await db.execute(
        select(Webhook).where(Webhook.id == webhook_id, Webhook.tenant_id == tenant_id)
    )
    webhook = result.scalar_one_or_none()
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook not found")

    if req.url is not None:
        webhook.url = req.url
    if req.events is not None:
        webhook.events = json.dumps(req.events)
    if req.is_active is not None:
        webhook.is_active = req.is_active

    await db.flush()

    await audit_log(
        db,
        actor=current_user["user_id"],
        action="update_webhook",
        entity_type="webhook",
        entity_id=webhook_id,
        details={"url": req.url, "events": req.events, "is_active": req.is_active},
    )

    return {
        "id": webhook.id,
        "url": webhook.url,
        "events": json.loads(webhook.events),
        "is_active": webhook.is_active,
    }


@router.delete("/webhooks/{webhook_id}")
async def delete_webhook(
    webhook_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(current_user)
    tenant_id = current_user.get("tenant_id")

    result = await db.execute(
        select(Webhook).where(Webhook.id == webhook_id, Webhook.tenant_id == tenant_id)
    )
    webhook = result.scalar_one_or_none()
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook not found")

    await db.delete(webhook)
    await db.flush()

    await audit_log(
        db,
        actor=current_user["user_id"],
        action="delete_webhook",
        entity_type="webhook",
        entity_id=webhook_id,
    )

    return {"success": True, "message": f"Webhook {webhook_id} deleted"}


@router.get("/webhooks/{webhook_id}/deliveries")
async def list_webhook_deliveries(
    webhook_id: int,
    page: int = 1,
    page_size: int = 20,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(current_user)
    tenant_id = current_user.get("tenant_id")

    result = await db.execute(
        select(Webhook).where(Webhook.id == webhook_id, Webhook.tenant_id == tenant_id)
    )
    webhook = result.scalar_one_or_none()
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook not found")

    from sqlalchemy import func as sql_func

    count_stmt = select(sql_func.count(WebhookDelivery.id)).where(
        WebhookDelivery.webhook_id == webhook_id,
        WebhookDelivery.tenant_id == tenant_id,
    )
    count_result = await db.execute(count_stmt)
    total = count_result.scalar() or 0

    stmt = (
        select(WebhookDelivery)
        .where(
            WebhookDelivery.webhook_id == webhook_id,
            WebhookDelivery.tenant_id == tenant_id,
        )
        .order_by(WebhookDelivery.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    deliveries_result = await db.execute(stmt)
    deliveries = deliveries_result.scalars().all()

    items = [
        {
            "id": d.id,
            "event_type": d.event_type,
            "response_status": d.response_status,
            "success": d.success,
            "attempt": d.attempt,
            "created_at": d.created_at.isoformat() if d.created_at else None,
        }
        for d in deliveries
    ]

    pages = (total + page_size - 1) // page_size if total > 0 else 0
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
    }


class LinkUserRequest(BaseModel):
    user_id: int


@router.patch("/doctors/{doctor_id}/link-user")
async def link_doctor_user(
    doctor_id: int,
    req: LinkUserRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(current_user)

    result = await db.execute(select(Doctor).where(Doctor.id == doctor_id))
    doctor = result.scalar_one_or_none()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")

    user_result = await db.execute(
        select(User).where(User.id == req.user_id)
    )
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.role != UserRole.DOCTOR:
        raise HTTPException(
            status_code=400, detail="User must have doctor role"
        )

    existing = await db.execute(
        select(Doctor).where(Doctor.user_id == req.user_id, Doctor.id != doctor_id)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=409, detail="User already linked to another doctor"
        )

    doctor.user_id = req.user_id
    await db.flush()

    await audit_log(
        db,
        actor=current_user["user_id"],
        action="link_doctor_user",
        entity_type="doctor",
        entity_id=doctor_id,
        details={"user_id": req.user_id},
    )

    return {"doctor_id": doctor_id, "user_id": req.user_id, "linked": True}
