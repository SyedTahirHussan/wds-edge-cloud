"""Test 5 (time-series consistency): residual against a one-step forecast.

The threshold adapts to a running robust scale: an exponentially weighted mean of
absolute residuals, where each residual is clipped at ``clip`` scales before it
enters the average (a Huber-type update), so outliers barely move the scale.
For Gaussian residuals E|r| = sigma * sqrt(2/pi), hence the 1.2533 factor.
"""

SQRT_PI_2 = 1.2533141373155


class ResidualTest:
    def __init__(self, k, lam, init_scale, min_scale, clip=3.0):
        self.k = k
        self.lam = lam
        self.scale = init_scale
        self.min_scale = min_scale
        self.clip = clip

    def sd_scale(self):
        return self.scale if self.scale > self.min_scale else self.min_scale

    def threshold(self):
        return self.k * SQRT_PI_2 * self.sd_scale()

    def score(self, r):
        """Residual in units of the current threshold (>1 means rejected)."""
        return abs(r) / self.threshold()

    def test(self, r):
        return abs(r) <= self.threshold()

    def learn(self, r):
        """Update the scale with an accepted residual."""
        a = abs(r)
        cap = self.clip * self.scale
        if a > cap:
            a = cap
        self.scale = (1.0 - self.lam) * self.scale + self.lam * a
