import json
import logging
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
from app.db.repository import PatientRepository, AppointmentRepository
from app.api.v1.dependencies import get_current_user
from app.core.audit import audit_log

logger = logging.getLogger("clinic.admin")

router = APIRouter(prefix="/admin", tags=["admin"])


def _require_admin(current_user: dict) -> None:
    if current_user["role"] != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")


async def _patient_ndjson(patient_id: int, db: AsyncSession):
    """Stream patient data and their appointments as NDJSON."""
    patient_repo = PatientRepository(db)
    patient = await patient_repo.get_by_id(patient_id)
    if not patient:
        return

    yield json.dumps({
        "type": "patient",
        "id": patient.id,
        "name": patient.name,
        "email": patient.email,
        "phone": patient.phone,
        "created_at": patient.created_at.isoformat() if patient.created_at else None,
    }) + "\n"

    appt_repo = AppointmentRepository(db)
    all_appointments = await appt_repo.list_all()
    for appt in all_appointments:
        if appt.patient_id == patient_id:
            yield json.dumps({
                "type": "appointment",
                "id": appt.id,
                "doctor_id": appt.doctor_id,
                "appointment_time": appt.appointment_time.isoformat() if appt.appointment_time else None,
                "status": appt.status.value,
                "notes": appt.notes,
                "created_at": appt.created_at.isoformat() if appt.created_at else None,
            }) + "\n"


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
        headers={"Content-Disposition": f'attachment; filename="patient_{patient_id}_export.ndjson"'},
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
