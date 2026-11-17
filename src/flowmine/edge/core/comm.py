"""Test 1 (communication): place each sample on the expected timestamp grid.

A sample is either on time, a duplicate of a slot already seen, out of order, or
it arrives after one or more empty slots (a gap). Timestamps are integer seconds.
"""

OK = 0
DUPLICATE = 1
OUT_OF_ORDER = 2


class TimeGrid:
    def __init__(self, period, origin=None):
        self.period = period
        self.origin = origin
        self.last_slot = None
        self.duplicates = 0
        self.out_of_order = 0
        self.missing = 0

    def slot(self, t):
        return (t - self.origin + self.period // 2) // self.period

    def time_of(self, slot):
        return self.origin + slot * self.period

    def check(self, t):
        """Return ``(status, n_missing_before)`` for a sample stamped ``t``."""
        if self.origin is None:
            self.origin = t
        s = self.slot(t)
        if self.last_slot is None:
            self.last_slot = s
            return OK, 0
        if s == self.last_slot:
            self.duplicates += 1
            return DUPLICATE, 0
        if s < self.last_slot:
            self.out_of_order += 1
            return OUT_OF_ORDER, 0
        gap = s - self.last_slot - 1
        self.missing += gap
        self.last_slot = s
        return OK, gap
