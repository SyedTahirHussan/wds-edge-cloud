"""Test 6 (local cross-check) for a tank station: level change against inlet flow.

Over the interval between two level samples the measured level change must agree
with the integrated inlet flow divided by the tank area: dh = sign * sum(Q dt) / A,
where ``sign`` is +1 when positive meter readings mean flow into the tank and -1 when
the meter's positive direction points out of it. A level that rises while the inlet
meter reads zero fails this test.
"""


class TankBalance:
    def __init__(self, area_m2, tol_m, sign=1.0, flow_to_m3s=0.001):
        self.area = area_m2
        self.sign = sign
        self.tol = tol_m
        self.k = flow_to_m3s
        self.volume = 0.0
        self.last_level = None
        self.last_flow = None
        self.last_flow_t = None

    def flow(self, t, q):
        """Integrate inlet flow with the trapezoidal rule between readings."""
        if self.last_flow is not None:
            self.volume += 0.5 * (self.last_flow + q) * self.k * (t - self.last_flow_t)
        self.last_flow = q
        self.last_flow_t = t

    def level(self, t, h):
        """Return True when the level step since the last level sample is consistent."""
        if self.last_flow is not None and self.last_flow_t < t:
            self.volume += self.last_flow * self.k * (t - self.last_flow_t)
            self.last_flow_t = t
        ok = True
        if self.last_level is not None and self.last_flow is not None:
            expected = self.sign * self.volume / self.area
            ok = abs((h - self.last_level) - expected) <= self.tol
        self.last_level = h
        self.volume = 0.0
        return ok
