import asyncio
import logging

from app.db.session import async_session_factory
from app.db.repository import AppointmentRepository, PatientRepository
from app.core.email import send_reminder_email

logger = logging.getLogger("clinic.scheduler.reminders")

POLL_INTERVAL_SECONDS = 300


async def send_due_reminders() -> None:
    async with async_session_factory() as session:
        repo = AppointmentRepository(session)
        due = await repo.get_due_reminders()
        for appt in due:
            patient_repo = PatientRepository(session)
            patient = await patient_repo.get_by_id(appt.patient_id)
            if patient:
                appt_detail = {
                    "doctor_id": appt.doctor_id,
                    "time_slot": appt.appointment_time.isoformat(),
                    "duration_minutes": appt.duration_minutes,
                }
                await send_reminder_email(patient.email, appt_detail)
                await repo.mark_reminder_sent(appt.id)
                logger.info(
                    "Reminder sent: appt_id=%s patient_id=%s",
                    appt.id,
                    patient.id,
                )
        await session.commit()


async def run_reminder_loop() -> None:
    logger.info("Reminder scheduler started")
    while True:
        try:
            await send_due_reminders()
        except Exception as e:
            logger.error("Reminder loop error: %s", e, exc_info=True)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    asyncio.run(run_reminder_loop())
