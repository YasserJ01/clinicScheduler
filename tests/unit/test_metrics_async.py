import pytest

pytest.importorskip("redis")
pytest.importorskip("app.core.metrics")

from unittest.mock import AsyncMock, patch

from app.core.metrics import MetricsCollector


@pytest.fixture
def mock_redis():
    return AsyncMock()


@pytest.fixture
def collector(mock_redis):
    with patch("app.core.metrics.aioredis.from_url", return_value=mock_redis):
        c = MetricsCollector(redis_url="redis://test:6379/0")
        c.redis = mock_redis
        return c


@pytest.mark.asyncio
async def test_increment_request_awaits_redis(collector, mock_redis):
    await collector.increment_request("GET", "/api/v1/doctors", 200)
    mock_redis.incr.assert_awaited_once()


@pytest.mark.asyncio
async def test_observe_duration_awaits_redis(collector, mock_redis):
    await collector.observe_duration("GET", "/api/v1/doctors", 0.123)
    assert mock_redis.incrbyfloat.await_count == 1
    assert mock_redis.incr.await_count >= 1


@pytest.mark.asyncio
async def test_increment_booking_awaits_redis(collector, mock_redis):
    await collector.increment_booking("success")
    mock_redis.incr.assert_awaited_once()


@pytest.mark.asyncio
async def test_set_circuit_breaker_state_awaits_redis(collector, mock_redis):
    await collector.set_circuit_breaker_state("db", 1)
    mock_redis.set.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_all_metrics_awaits_redis(collector, mock_redis):
    mock_redis.keys.return_value = []
    result = await collector.get_all_metrics()
    assert isinstance(result, str)
    assert "# HELP" in result
    assert "# TYPE" in result
    mock_redis.keys.assert_awaited()
