import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("app.db.repository")

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

from app.db.repository import AppointmentRepository


@pytest.mark.asyncio
async def test_check_conflict_includes_lower_bound():
    session = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=result_mock)

    repo = AppointmentRepository(session)
    appointment_time = datetime(2026, 6, 15, 10, 0, 0)

    await repo.check_conflict(
        doctor_id=1, appointment_time=appointment_time, duration_minutes=30
    )

    session.execute.assert_awaited_once()
    call_args = session.execute.call_args
    stmt = call_args[0][0]

    compiled = stmt.compile(compile_kwargs={"literal_binds": True})
    sql = str(compiled).lower()

    assert "appointment_time >=" in sql or "appointment_time >" in sql
    assert "appointment_time <" in sql
