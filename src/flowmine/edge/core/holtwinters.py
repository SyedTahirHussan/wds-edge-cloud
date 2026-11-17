"""Streaming double-seasonal Holt-Winters forecaster (additive form of Taylor, 2003).

Taylor's method has a level, a within-day seasonal index d (season s1) and a
within-week index w (season s2), plus an AR(1) adjustment on the one-step error:

    e_t   = y_t - (l_{t-1} + d_{t-s1} + w_{t-s2})
    l_t   = alpha * (y_t - d_{t-s1} - w_{t-s2}) + (1 - alpha) * l_{t-1}
    d_t   = delta * (y_t - l_t - w_{t-s2})     + (1 - delta) * d_{t-s1}
    w_t   = omega * (y_t - l_t - d_{t-s1})     + (1 - omega) * w_{t-s2}
    yhat_{t+h} = l_t + d_{t+h-s1} + w_{t+h-s2} + phi^h * e_t

Taylor uses multiplicative seasonality and a trend; the additive, trend-free form is
used here because pressures and flows can sit near zero (pump off) and a trend term
diverges over long gaps. A missing or rejected sample advances time without changing
level or seasonal indices, and the AR error decays by phi, so rejected values never
enter the state. Memory: s1 + s2 floats.
"""

from array import array


class HoltWinters:
    kind = "hw"

    def __init__(self, period, s1, s2, alpha, delta, omega, phi, t0=0):
        self.period = period
        self.s1 = s1
        self.s2 = s2
        self.alpha = alpha
        self.delta = delta
        self.omega = omega
        self.phi = phi
        self.t0 = t0
        self.level = 0.0
        self.e = 0.0
        self.d = array("f", [0.0] * s1)
        self.w = array("f", [0.0] * s2)
        self.k = None

    def slot(self, t):
        return (t - self.t0) // self.period

    def _step(self, x, k):
        ds = self.d[k % self.s1]
        ws = self.w[k % self.s2]
        if x is None:
            self.e = self.phi * self.e
        else:
            l_prev = self.level
            self.e = x - (l_prev + ds + ws)
            lv = self.alpha * (x - ds - ws) + (1.0 - self.alpha) * l_prev
            self.d[k % self.s1] = self.delta * (x - lv - ws) + (1.0 - self.delta) * ds
            self.w[k % self.s2] = self.omega * (x - lv - ds) + (1.0 - self.omega) * ws
            self.level = lv
        self.k = k

    def update(self, x, t):
        k = self.slot(t)
        if self.k is None:
            self.k = k - 1
        if k <= self.k:
            return
        while self.k < k - 1:
            self._step(None, self.k + 1)
        self._step(x, k)

    def predict(self, h):
        k = self.k + h
        ar = self.e
        for _ in range(h):
            ar = ar * self.phi
        return self.level + self.d[k % self.s1] + self.w[k % self.s2] + ar

    def forecast_at(self, t):
        h = self.slot(t) - self.k
        if h < 1:
            h = 1
        return self.predict(h)

    def relevel(self, x):
        """Move the level so the forecast for the next slot equals ``x``."""
        k = self.k + 1
        self.level = x - self.d[k % self.s1] - self.w[k % self.s2]
        self.e = 0.0

    def state(self):
        return {
            "kind": self.kind,
            "period": self.period,
            "s1": self.s1,
            "s2": self.s2,
            "alpha": self.alpha,
            "delta": self.delta,
            "omega": self.omega,
            "phi": self.phi,
            "t0": self.t0,
            "k": self.k,
            "level": self.level,
            "e": self.e,
            "d": list(self.d),
            "w": list(self.w),
        }

    def load(self, s):
        self.period = s["period"]
        self.s1 = s["s1"]
        self.s2 = s["s2"]
        self.alpha = s["alpha"]
        self.delta = s["delta"]
        self.omega = s["omega"]
        self.phi = s["phi"]
        self.t0 = s["t0"]
        self.k = s["k"]
        self.level = s["level"]
        self.e = s["e"]
        self.d = array("f", s["d"])
        self.w = array("f", s["w"])
