import math
import logging
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, text
from app.db.session import get_db
from app.db.repository import AppointmentRepository, PatientRepository, DoctorRepository
from app.api.v1.dependencies import get_current_user
from app.models import Appointment, Patient, Doctor, AuditLog, AppointmentStatus

logger = logging.getLogger("clinic.analytics")

router = APIRouter(prefix="/admin/analytics", tags=["admin-analytics"])


def _require_admin(current_user: dict) -> None:
    if current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")


@router.get("/summary")
async def get_analytics_summary(
    from_date: str | None = Query(None),
    to_date: str | None = Query(None),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(current_user)

    where_clauses = []
    if from_date:
        from_dt = datetime.fromisoformat(from_date.replace("Z", "+00:00")).replace(tzinfo=None)
        where_clauses.append(Appointment.appointment_time >= from_dt)
    if to_date:
        to_dt = datetime.fromisoformat(to_date.replace("Z", "+00:00")).replace(tzinfo=None)
        where_clauses.append(Appointment.appointment_time <= to_dt)

    total_appts_stmt = select(func.count(Appointment.id))
    if where_clauses:
        total_appts_stmt = total_appts_stmt.where(*where_clauses)
    total_appts_result = await db.execute(total_appts_stmt)
    total_appointments = total_appts_result.scalar() or 0

    cancelled_stmt = select(func.count(Appointment.id)).where(
        Appointment.status == AppointmentStatus.CANCELLED
    )
    if where_clauses:
        cancelled_stmt = cancelled_stmt.where(*where_clauses)
    cancelled_result = await db.execute(cancelled_stmt)
    cancelled_count = cancelled_result.scalar() or 0

    cancellation_rate = (cancelled_count / total_appointments * 100) if total_appointments > 0 else 0

    avg_duration_stmt = select(func.avg(Appointment.duration_minutes))
    if where_clauses:
        avg_duration_stmt = avg_duration_stmt.where(*where_clauses)
    avg_duration_result = await db.execute(avg_duration_stmt)
    avg_duration = round(avg_duration_result.scalar() or 0, 1)

    total_patients_result = await db.execute(select(func.count(Patient.id)))
    total_patients = total_patients_result.scalar() or 0

    total_doctors_result = await db.execute(select(func.count(Doctor.id)))
    total_doctors = total_doctors_result.scalar() or 0

    return {
        "total_appointments": total_appointments,
        "total_patients": total_patients,
        "total_doctors": total_doctors,
        "cancelled_appointments": cancelled_count,
        "cancellation_rate": round(cancellation_rate, 2),
        "avg_duration_minutes": avg_duration,
        "period": {"from": from_date, "to": to_date},
    }


@router.get("/doctors/{doctor_id}/utilisation")
async def get_doctor_utilisation(
    doctor_id: int,
    from_date: str | None = Query(None),
    to_date: str | None = Query(None),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(current_user)

    doctor_repo = DoctorRepository(db)
    doctor = await doctor_repo.get_by_id(doctor_id)
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")

    now = datetime.utcnow()
    default_from = now - timedelta(days=30)
    default_to = now

    from_dt = (
        datetime.fromisoformat(from_date.replace("Z", "+00:00")).replace(tzinfo=None)
        if from_date
        else default_from
    )
    to_dt = (
        datetime.fromisoformat(to_date.replace("Z", "+00:00")).replace(tzinfo=None)
        if to_date
        else default_to
    )

    booked_stmt = select(func.count(Appointment.id)).where(
        Appointment.doctor_id == doctor_id,
        Appointment.appointment_time >= from_dt,
        Appointment.appointment_time <= to_dt,
        Appointment.status != AppointmentStatus.CANCELLED,
    )
    booked_result = await db.execute(booked_stmt)
    booked_slots = booked_result.scalar() or 0

    total_days = (to_dt - from_dt).days + 1
    schedule = await doctor_repo.get_schedule(doctor_id)
    if schedule:
        total_slots = sum(
            int((s.end_time.hour * 60 + s.end_time.minute - s.start_time.hour * 60 - s.start_time.minute) / 30)
            for s in schedule
        ) * total_days
    else:
        total_slots = 18 * total_days

    utilisation = (booked_slots / total_slots * 100) if total_slots > 0 else 0

    return {
        "doctor_id": doctor_id,
        "doctor_name": doctor.name,
        "period": {"from": from_dt.isoformat(), "to": to_dt.isoformat()},
        "booked_slots": booked_slots,
        "total_available_slots": total_slots,
        "utilisation_rate": round(utilisation, 2),
    }


@router.get("/peak-hours")
async def get_peak_hours(
    days: int = Query(30, ge=1, le=365),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(current_user)

    from_dt = datetime.utcnow() - timedelta(days=days)

    result = await db.execute(
        text("""
        SELECT EXTRACT(HOUR FROM appointment_time) AS hour, COUNT(*) AS bookings
        FROM appointments
        WHERE appointment_time >= :from_dt
          AND status != 'cancelled'
        GROUP BY hour
        ORDER BY hour
        """),
        {"from_dt": from_dt},
    )
    rows = result.fetchall()

    histogram = {str(h): c for h, c in rows}
    peak_hour = max(histogram, key=lambda k: histogram[k]) if histogram else None

    return {
        "period_days": days,
        "histogram": histogram,
        "peak_hour": int(peak_hour) if peak_hour else None,
        "peak_hour_bookings": histogram.get(peak_hour, 0) if peak_hour else 0,
    }


@router.get("/patients/{patient_id}/history")
async def get_patient_history(
    patient_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(current_user)

    patient_repo = PatientRepository(db)
    patient = await patient_repo.get_by_id(patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    appt_repo = AppointmentRepository(db)
    appointments, total = await appt_repo.list_paginated(
        page=1, page_size=100, patient_id=patient_id
    )

    history = []
    for appt in appointments:
        doctor = await db.execute(select(Doctor).where(Doctor.id == appt.doctor_id))
        d = doctor.scalar_one_or_none()
        history.append({
            "id": appt.id,
            "doctor_name": d.name if d else "Unknown",
            "doctor_specialty": d.specialty if d else "Unknown",
            "appointment_time": appt.appointment_time.isoformat(),
            "duration_minutes": appt.duration_minutes,
            "status": appt.status.value,
            "notes": appt.notes,
            "created_at": appt.created_at.isoformat() if appt.created_at else None,
        })

    return {
        "patient_id": patient_id,
        "patient_name": patient.name,
        "total_appointments": len(history),
        "history": history,
    }


@router.get("/audit-log")
async def get_audit_log(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    actor: str | None = Query(None),
    action: str | None = Query(None),
    from_date: str | None = Query(None),
    to_date: str | None = Query(None),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(current_user)

    where_clauses = []
    if actor:
        where_clauses.append(AuditLog.actor.ilike(f"%{actor}%"))
    if action:
        where_clauses.append(AuditLog.action == action)
    if from_date:
        from_dt = datetime.fromisoformat(from_date.replace("Z", "+00:00")).replace(tzinfo=None)
        where_clauses.append(AuditLog.created_at >= from_dt)
    if to_date:
        to_dt = datetime.fromisoformat(to_date.replace("Z", "+00:00")).replace(tzinfo=None)
        where_clauses.append(AuditLog.created_at <= to_dt)

    count_stmt = select(func.count()).select_from(AuditLog)
    if where_clauses:
        count_stmt = count_stmt.where(*where_clauses)
    count_result = await db.execute(count_stmt)
    total = count_result.scalar() or 0

    stmt = select(AuditLog).order_by(AuditLog.created_at.desc())
    if where_clauses:
        stmt = stmt.where(*where_clauses)
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    entries = result.scalars().all()

    items = [
        {
            "id": e.id,
            "actor": e.actor,
            "action": e.action,
            "entity_type": e.entity_type,
            "entity_id": e.entity_id,
            "details": e.details,
            "outcome": e.outcome,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in entries
    ]

    pages = math.ceil(total / page_size) if total > 0 else 0
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
    }
