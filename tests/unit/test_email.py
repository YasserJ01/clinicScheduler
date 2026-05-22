import pytest
from app.core.email import (
    NullEmailService,
    get_email_service,
    send_booking_confirmation,
    send_cancellation_email,
    send_confirmation_email,
    send_reminder_email,
)


class TestNullEmailService:
    @pytest.mark.asyncio
    async def test_send_does_not_raise(self):
        service = NullEmailService()
        await service.send("test@example.com", "Test", "Body")

    @pytest.mark.asyncio
    async def test_send_returns_none(self):
        service = NullEmailService()
        result = await service.send("test@example.com", "Test", "Body")
        assert result is None


class TestGetEmailService:
    def test_default_is_null(self):
        service = get_email_service()
        assert isinstance(service, NullEmailService)


class TestEmailFunctions:
    @pytest.mark.asyncio
    async def test_send_booking_confirmation(self):
        appt = {
            "doctor_id": 1,
            "time_slot": "2026-07-01T09:00:00",
            "duration_minutes": 30,
            "status": "scheduled",
        }
        await send_booking_confirmation("patient@example.com", appt)

    @pytest.mark.asyncio
    async def test_send_cancellation_email(self):
        appt = {
            "doctor_id": 1,
            "time_slot": "2026-07-01T09:00:00",
            "duration_minutes": 30,
            "status": "cancelled",
        }
        await send_cancellation_email("patient@example.com", appt)

    @pytest.mark.asyncio
    async def test_send_confirmation_email(self):
        appt = {
            "doctor_id": 1,
            "time_slot": "2026-07-01T09:00:00",
            "duration_minutes": 30,
            "status": "confirmed",
        }
        await send_confirmation_email("patient@example.com", appt)

    @pytest.mark.asyncio
    async def test_send_reminder_email(self):
        appt = {
            "doctor_id": 1,
            "time_slot": "2026-07-01T09:00:00",
            "duration_minutes": 30,
            "status": "scheduled",
        }
        await send_reminder_email("patient@example.com", appt)
