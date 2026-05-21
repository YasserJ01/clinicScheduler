import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("app.db.repository")

from unittest.mock import AsyncMock, MagicMock

from app.db.repository import PatientRepository


@pytest.mark.asyncio
async def test_get_or_create_by_email_looks_up_by_email():
    session = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=result_mock)

    repo = PatientRepository(session)
    await repo.get_or_create_by_email(name="John Smith", email="john@example.com")

    session.execute.assert_awaited_once()
    call_args = session.execute.call_args
    stmt = call_args[0][0]

    compiled = stmt.compile(compile_kwargs={"literal_binds": True})
    sql = str(compiled).lower()

    assert "patients.email =" in sql
    assert "patients.name =" not in sql


@pytest.mark.asyncio
async def test_get_or_create_by_email_returns_existing_patient():
    session = AsyncMock()
    existing_patient = MagicMock()
    existing_patient.id = 42
    existing_patient.name = "John Smith"
    existing_patient.email = "john@example.com"

    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = existing_patient
    session.execute = AsyncMock(return_value=result_mock)

    repo = PatientRepository(session)
    patient = await repo.get_or_create_by_email(
        name="John Smith", email="john@example.com"
    )

    assert patient == existing_patient
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_get_or_create_by_email_creates_new_patient():
    session = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=result_mock)

    repo = PatientRepository(session)
    await repo.get_or_create_by_email(name="Jane Doe", email="jane@example.com")

    session.add.assert_called_once()
