"""Tests 2-4: physical range, rate of change and flatline.

Each test looks only at the sensor's own recent values, so it can run on the
station without any model.
"""

from .ringbuf import RingBuffer


def in_range(x, lo, hi):
    return lo <= x <= hi


def rate_ok(x, last, dt, max_rate):
    """``max_rate`` is in physical units per second; ``last`` None passes."""
    if last is None or dt <= 0:
        return True
    return abs(x - last) <= max_rate * dt


class Flatline:
    """Flags a sample when the last ``window`` raw values span at most ``tol``.

    ``tol`` is set from the sensor resolution, so quantisation alone cannot raise
    the flag on a live signal. Values inside ``rest_band`` of zero are exempt: a
    stopped pump legitimately reads a constant zero flow.
    """

    def __init__(self, window, tol, rest_band=0.0):
        self.buf = RingBuffer(window)
        self.tol = tol
        self.rest_band = rest_band

    def push(self, x):
        self.buf.push(x)
        if not self.buf.full() or abs(x) <= self.rest_band:
            return False
        lo = hi = self.buf.get(0)
        for i in range(1, self.buf.size):
            v = self.buf.get(i)
            if v < lo:
                lo = v
            elif v > hi:
                hi = v
        return (hi - lo) <= self.tol
