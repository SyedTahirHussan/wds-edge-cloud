"""Seasonal naive forecaster: the value at the same slot one season ago (default a week).

This is the floor every other forecaster has to beat. Memory: one float per slot of
the season (2016 floats, about 8 kB, for a week at 5 minutes).
"""

from array import array


class SeasonalNaive:
    kind = "snaive"

    def __init__(self, period, season, t0=0):
        self.period = period
        self.season = season
        self.t0 = t0
        self.buf = array("f", [0.0] * season)
        self.k = None

    def slot(self, t):
        return (t - self.t0) // self.period

    def update(self, x, t):
        """Store ``x`` for the slot of ``t``; ``None`` keeps last season's value."""
        k = self.slot(t)
        if self.k is not None and k <= self.k:
            return
        if x is not None:
            self.buf[k % self.season] = x
        self.k = k

    def predict(self, h):
        return self.buf[(self.k + h) % self.season]

    def forecast_at(self, t):
        h = self.slot(t) - self.k
        if h < 1:
            h = 1
        return self.predict(h)

    def relevel(self, x):
        """A seasonal naive model has no level to move."""
        return None

    def state(self):
        return {
            "kind": self.kind,
            "period": self.period,
            "season": self.season,
            "t0": self.t0,
            "k": self.k,
            "buf": list(self.buf),
        }

    def load(self, s):
        self.period = s["period"]
        self.season = s["season"]
        self.t0 = s["t0"]
        self.k = s["k"]
        self.buf = array("f", s["buf"])
