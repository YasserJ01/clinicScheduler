import pytest
import asyncio
from app.core.circuit_breaker import CircuitBreaker, CircuitBreakerError, CircuitState


class TestCircuitBreakerStateTransitions:
    def test_initial_state_is_closed(self):
        cb = CircuitBreaker()
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_closed_to_open_after_threshold_failures(self):
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=10.0)

        async def failing_func():
            raise ConnectionError("DB down")

        for _ in range(3):
            with pytest.raises(ConnectionError):
                await cb.call(failing_func)

        assert cb.state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_open_raises_circuit_breaker_error(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=60.0)

        async def failing_func():
            raise ConnectionError("DB down")

        with pytest.raises(ConnectionError):
            await cb.call(failing_func)

        assert cb.state == CircuitState.OPEN

        with pytest.raises(CircuitBreakerError):
            await cb.call(failing_func)

    @pytest.mark.asyncio
    async def test_open_to_half_open_after_timeout(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.1)

        async def failing_func():
            raise ConnectionError("DB down")

        with pytest.raises(ConnectionError):
            await cb.call(failing_func)

        assert cb.state == CircuitState.OPEN

        await asyncio.sleep(0.15)
        assert cb.state == CircuitState.HALF_OPEN

    @pytest.mark.asyncio
    async def test_half_open_to_closed_on_success(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.1)

        async def failing_func():
            raise ConnectionError("DB down")

        async def success_func():
            return "ok"

        with pytest.raises(ConnectionError):
            await cb.call(failing_func)

        await asyncio.sleep(0.15)
        assert cb.state == CircuitState.HALF_OPEN

        result = await cb.call(success_func)
        assert result == "ok"
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_half_open_to_open_on_failure(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.1)

        async def failing_func():
            raise ConnectionError("DB down")

        with pytest.raises(ConnectionError):
            await cb.call(failing_func)

        await asyncio.sleep(0.15)
        assert cb.state == CircuitState.HALF_OPEN

        with pytest.raises(ConnectionError):
            await cb.call(failing_func)

        assert cb.state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_success_resets_failure_count(self):
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=10.0)

        async def failing_func():
            raise ConnectionError("DB down")

        async def success_func():
            return "ok"

        for _ in range(2):
            with pytest.raises(ConnectionError):
                await cb.call(failing_func)

        assert cb._failure_count == 2

        await cb.call(success_func)
        assert cb._failure_count == 0

    @pytest.mark.asyncio
    async def test_call_passes_arguments(self):
        cb = CircuitBreaker()

        async def add(a, b):
            return a + b

        result = await cb.call(add, 3, 4)
        assert result == 7

        result = await cb.call(add, a=10, b=20)
        assert result == 30
