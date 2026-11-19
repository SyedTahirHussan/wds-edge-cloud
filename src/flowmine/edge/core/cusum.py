"""Two-sided CUSUM (Page, 1954) on standardised one-step forecast residuals.

For a residual z_t in units of its running standard deviation,

    S+_t = max(0, S+_{t-1} + z_t - k)        S-_t = max(0, S-_{t-1} - z_t - k)

and the test is in alarm while max(S+, S-) > h. The allowance k is half the mean
shift (in sd) the test is tuned for; h sets the false-alarm rate, and both are
calibrated on the calibration week. Each z is clipped to +-``clip`` first, so one
outlier that slipped through the residual test cannot raise an alarm by itself.

A two-sided CUSUM was preferred to Page-Hinkley because it is the same statistic
with a fixed reference (zero mean of the residual) and a symmetric pair of sums, so
upward and downward drifts get the same treatment; it needs two floats of memory.
"""


class Cusum:
    def __init__(self, k, h, clip=4.0):
        self.k = k
        self.h = h
        self.clip = clip
        self.sp = 0.0
        self.sn = 0.0

    def update(self, z):
        """Add one standardised residual; returns True while in alarm."""
        if z > self.clip:
            z = self.clip
        elif z < -self.clip:
            z = -self.clip
        sp = self.sp + z - self.k
        sn = self.sn - z - self.k
        self.sp = sp if sp > 0.0 else 0.0
        self.sn = sn if sn > 0.0 else 0.0
        return self.alarm()

    def alarm(self):
        return self.sp > self.h or self.sn > self.h

    def stat(self):
        return self.sp if self.sp > self.sn else self.sn

    def reset(self):
        self.sp = 0.0
        self.sn = 0.0
