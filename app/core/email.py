import logging
from abc import ABC, abstractmethod
from app.config import settings

logger = logging.getLogger("clinic.email")


class EmailService(ABC):
    @abstractmethod
    async def send(self, to: str, subject: str, body: str) -> None: ...


class NullEmailService(EmailService):
    async def send(self, to: str, subject: str, body: str) -> None:
        logger.info("NULL EMAIL: to=%s subject=%s", to, subject)


class SMTPEmailService(EmailService):
    def __init__(self):
        self.host = settings.SMTP_HOST
        self.port = settings.SMTP_PORT
        self.from_email = settings.FROM_EMAIL

    async def send(self, to: str, subject: str, body: str) -> None:
        try:
            import aiosmtplib
            from email.mime.text import MIMEText

            msg = MIMEText(body)
            msg["Subject"] = subject
            msg["From"] = self.from_email
            msg["To"] = to

            smtp = aiosmtplib.SMTP(hostname=self.host, port=self.port)
            await smtp.connect()
            await smtp.send_message(msg)
            await smtp.quit()
            logger.info("Email sent via SMTP: to=%s subject=%s", to, subject)
        except Exception as e:
            logger.error("Failed to send email via SMTP: %s", e)


class SendGridEmailService(EmailService):
    def __init__(self):
        self.api_key = settings.SENDGRID_API_KEY
        self.from_email = settings.FROM_EMAIL

    async def send(self, to: str, subject: str, body: str) -> None:
        try:
            import httpx

            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "personalizations": [{"to": [{"email": to}]}],
                "from": {"email": self.from_email},
                "subject": subject,
                "content": [{"type": "text/plain", "value": body}],
            }
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://api.sendgrid.com/v3/mail/send",
                    headers=headers,
                    json=payload,
                )
                if resp.status_code >= 400:
                    logger.error("SendGrid error: %s %s", resp.status_code, resp.text)
                else:
                    logger.info(
                        "Email sent via SendGrid: to=%s subject=%s", to, subject
                    )
        except Exception as e:
            logger.error("Failed to send email via SendGrid: %s", e)


def get_email_service() -> EmailService:
    provider = settings.EMAIL_PROVIDER
    if provider == "smtp":
        return SMTPEmailService()
    elif provider == "sendgrid":
        return SendGridEmailService()
    return NullEmailService()


async def send_booking_confirmation(patient_email: str, appointment: dict) -> None:
    service = get_email_service()
    subject = "Appointment Confirmation"
    body = (
        f"Your appointment has been booked successfully.\n\n"
        f"Doctor ID: {appointment['doctor_id']}\n"
        f"Time: {appointment['time_slot']}\n"
        f"Duration: {appointment['duration_minutes']} minutes\n"
        f"Status: {appointment['status']}"
    )
    await service.send(patient_email, subject, body)


async def send_cancellation_email(patient_email: str, appointment: dict) -> None:
    service = get_email_service()
    subject = "Appointment Cancelled"
    body = (
        f"Your appointment has been cancelled.\n\n"
        f"Doctor ID: {appointment['doctor_id']}\n"
        f"Original Time: {appointment['time_slot']}"
    )
    await service.send(patient_email, subject, body)


async def send_confirmation_email(patient_email: str, appointment: dict) -> None:
    service = get_email_service()
    subject = "Appointment Confirmed by Doctor"
    body = (
        f"Your appointment has been confirmed.\n\n"
        f"Doctor ID: {appointment['doctor_id']}\n"
        f"Time: {appointment['time_slot']}\n"
        f"Duration: {appointment['duration_minutes']} minutes"
    )
    await service.send(patient_email, subject, body)


async def send_reminder_email(patient_email: str, appointment: dict) -> None:
    service = get_email_service()
    subject = "Appointment Reminder — Tomorrow"
    body = (
        f"Reminder: You have an appointment tomorrow.\n\n"
        f"Doctor ID: {appointment['doctor_id']}\n"
        f"Time: {appointment['time_slot']}\n"
        f"Duration: {appointment['duration_minutes']} minutes"
    )
    await service.send(patient_email, subject, body)
