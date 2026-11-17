"""Per-sensor validation and reconstruction, in the order of the UPC methodology.

Tests run from cheapest to deepest: communication (grid), physical range, rate of
change, flatline, then the residual against a one-step forecast. A sample that fails
any test, or a slot with no sample, is replaced by the forecast and marked
RECONSTRUCTED; the raw value is kept next to it. Rejected values never enter the
forecaster state: the forecaster is advanced with ``None`` instead.

Samples rejected by the residual test alone are held back (at most ``shift_after``
of them, a fixed buffer). If the next sample passes, the held samples were a
transient and are released as reconstructed. If ``shift_after`` residual-only
rejections with residuals of the same sign occur in a row, the signal has moved to a
new level: a pump switching, a leak, or a bias fault, which the station cannot tell
apart (residuals of mixed sign, as in a noise burst, are rejected). The held samples are then
released with their raw values and the SHIFT flag, the forecaster is re-levelled,
and the decision is left to the cloud. The price is a delay of up to
``shift_after`` samples before a residual rejection is released.

Each output record is ``(t, raw, value, quality, score)``; ``score`` is the residual
in units of the current threshold (0 when no forecast was available).
"""

from . import checks, comm, quality
from .residual import ResidualTest


class SensorPipeline:
    def __init__(self, meta, model, model_version=0):
        self.meta = meta
        self.model = model
        self.mv = model_version
        self.period = meta["period"]
        self.grid = comm.TimeGrid(self.period, meta.get("t0"))
        self.flat = checks.Flatline(
            meta["flat_window"], meta["flat_tol"], meta.get("rest_band", 0.0)
        )
        self.res = ResidualTest(meta["k"], meta["lam"], meta["init_scale"], meta["min_scale"])
        self.shift_after = meta["shift_after"]
        self.max_recon = meta["max_recon"]
        self.last_good = None
        self.last_good_t = None
        self.pending = []
        self.unvalidated = 0
        self.dropped = 0

    def set_model(self, model, model_version):
        self.model = model
        self.mv = model_version

    def process(self, t, x):
        """Validate one raw sample (``x`` None means the sensor reported no value)."""
        status, gap = self.grid.check(t)
        if status != comm.OK:
            self.dropped += 1
            return []
        out = []
        if gap or x is None:
            out.extend(self.flush())
        for i in range(gap):
            out.append(self._reconstruct(t - (gap - i) * self.period, None, quality.MISSING, 0.0))
        if x is None:
            out.append(self._reconstruct(t, None, quality.MISSING, 0.0))
        else:
            out.extend(self._sample(t, x))
        return out

    def flush(self):
        """Release held samples as reconstructed (end of data, or a gap interrupts them)."""
        out = []
        for t, x, pred, score, _r in self.pending:
            out.append(self._held(t, x, pred, score))
        self.pending = []
        return out

    def _held(self, t, x, pred, score):
        flags = quality.MODEL_RESIDUAL
        if pred is None or self.unvalidated > self.max_recon:
            return (t, x, None, quality.make(flags, self.mv), score)
        return (t, x, pred, quality.make(flags | quality.RECONSTRUCTED, self.mv), score)

    def _forecast(self, t):
        if self.model is None or self.model.k is None:
            return None
        return self.model.forecast_at(t)

    def _reconstruct(self, t, raw, flags, score):
        self.unvalidated += 1
        pred = self._forecast(t)
        if self.model is not None:
            self.model.update(None, t)
        if pred is None or self.unvalidated > self.max_recon:
            return (t, raw, None, quality.make(flags, self.mv), score)
        return (t, raw, pred, quality.make(flags | quality.RECONSTRUCTED, self.mv), score)

    def _sample(self, t, x):
        m = self.meta
        f = 0
        if not checks.in_range(x, m["lo"], m["hi"]):
            f |= quality.OUT_OF_RANGE
        dt = 0 if self.last_good_t is None else t - self.last_good_t
        if not checks.rate_ok(x, self.last_good, dt, m["max_rate"]):
            f |= quality.RATE
        if self.flat.push(x):
            f |= quality.FLATLINE
        pred = self._forecast(t)
        score = 0.0
        r = 0.0
        if pred is not None:
            r = x - pred
            score = self.res.score(r)
            if score > 1.0:
                f |= quality.MODEL_RESIDUAL
        if f == quality.MODEL_RESIDUAL:
            self.unvalidated += 1
            self.pending.append((t, x, pred, score, r))
            if self.model is not None:
                self.model.update(None, t)
            if len(self.pending) < self.shift_after:
                return []
            pos = 0
            for p in self.pending:
                if p[4] > 0:
                    pos += 1
            if pos == 0 or pos == len(self.pending):
                return self._shift()
            return self.flush()
        out = self.flush()
        if f == 0:
            out.append(self._accept(t, x, r, score, 0))
        else:
            out.append(self._reconstruct(t, x, f, score))
        return out

    def _shift(self):
        out = []
        last = self.pending[-1]
        self.model.relevel(last[1])
        for t, x, _pred, score, _r in self.pending:
            out.append((t, x, x, quality.make(quality.SHIFT, self.mv), score))
        self.pending = []
        self.unvalidated = 0
        self.last_good = last[1]
        self.last_good_t = last[0]
        return out

    def _accept(self, t, x, r, score, flags):
        if self.model is not None:
            self.model.update(x, t)
        if flags == 0:
            self.res.learn(r)
        self.unvalidated = 0
        self.last_good = x
        self.last_good_t = t
        return (t, x, x, quality.make(flags, self.mv), score)
