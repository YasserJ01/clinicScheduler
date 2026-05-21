from datetime import datetime, timedelta


class TestDurationOverlap:
    """Test range-based conflict detection logic for appointment durations.

    These are pure logic tests that don't require database or app imports.
    """

    @staticmethod
    def _overlaps(existing_start, existing_duration, new_start, new_duration):
        existing_end = existing_start + timedelta(minutes=existing_duration)
        new_end = new_start + timedelta(minutes=new_duration)
        return new_start < existing_end and new_end > existing_start

    def test_no_overlap_same_day_different_times(self):
        existing_start = datetime(2026, 6, 15, 9, 0)
        new_start = datetime(2026, 6, 15, 10, 0)
        assert not self._overlaps(existing_start, 30, new_start, 30)

    def test_overlap_partial(self):
        existing_start = datetime(2026, 6, 15, 9, 0)
        new_start = datetime(2026, 6, 15, 9, 30)
        assert self._overlaps(existing_start, 60, new_start, 30)

    def test_overlap_new_contains_existing(self):
        existing_start = datetime(2026, 6, 15, 9, 30)
        new_start = datetime(2026, 6, 15, 9, 0)
        assert self._overlaps(existing_start, 30, new_start, 60)

    def test_overlap_existing_contains_new(self):
        existing_start = datetime(2026, 6, 15, 9, 0)
        new_start = datetime(2026, 6, 15, 9, 30)
        assert self._overlaps(existing_start, 120, new_start, 30)

    def test_exact_boundary_no_overlap(self):
        existing_start = datetime(2026, 6, 15, 9, 0)
        new_start = datetime(2026, 6, 15, 9, 30)
        assert not self._overlaps(existing_start, 30, new_start, 30)

    def test_overlap_at_start_boundary(self):
        existing_start = datetime(2026, 6, 15, 9, 0)
        new_start = datetime(2026, 6, 15, 8, 45)
        assert self._overlaps(existing_start, 30, new_start, 30)

    def test_overlap_at_end_boundary(self):
        existing_start = datetime(2026, 6, 15, 9, 0)
        new_start = datetime(2026, 6, 15, 9, 15)
        assert self._overlaps(existing_start, 30, new_start, 30)


class TestAvailableSlots:
    """Test available slot calculation logic."""

    @staticmethod
    def _calculate_available(
        booked_ranges, slot_start, slot_end, duration_minutes, delta_minutes=30
    ):
        available = []
        current = slot_start
        delta = timedelta(minutes=delta_minutes)
        while current + timedelta(minutes=duration_minutes) <= slot_end:
            proposed_end = current + timedelta(minutes=duration_minutes)
            overlaps = any(
                current < booked_end and proposed_end > booked_start
                for booked_start, booked_end in booked_ranges
            )
            if not overlaps:
                available.append(current)
            current += delta
        return available

    def test_all_slots_available_when_no_bookings(self):
        booked_ranges = []
        slot_start = datetime(2026, 6, 15, 8, 0)
        slot_end = datetime(2026, 6, 15, 17, 0)

        available = self._calculate_available(booked_ranges, slot_start, slot_end, 30)
        assert len(available) == 18

    def test_slots_blocked_by_booking(self):
        booking_start = datetime(2026, 6, 15, 9, 0)
        booking_duration = 60
        booking_end = booking_start + timedelta(minutes=booking_duration)
        booked_ranges = [(booking_start, booking_end)]

        slot_start = datetime(2026, 6, 15, 8, 0)
        slot_end = datetime(2026, 6, 15, 17, 0)

        available = self._calculate_available(booked_ranges, slot_start, slot_end, 30)

        assert datetime(2026, 6, 15, 8, 0) in available
        assert datetime(2026, 6, 15, 8, 30) in available
        assert datetime(2026, 6, 15, 9, 0) not in available
        assert datetime(2026, 6, 15, 9, 30) not in available
        assert datetime(2026, 6, 15, 10, 0) in available

    def test_longer_duration_reduces_available_slots(self):
        booked_ranges = []
        slot_start = datetime(2026, 6, 15, 8, 0)
        slot_end = datetime(2026, 6, 15, 17, 0)

        available_30 = self._calculate_available(
            booked_ranges, slot_start, slot_end, 30
        )
        available_60 = self._calculate_available(
            booked_ranges, slot_start, slot_end, 60
        )

        assert len(available_60) < len(available_30)
