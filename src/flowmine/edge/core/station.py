"""A station: several sensor pipelines plus the station-level cross-check.

Samples must be offered in time order across the station's sensors. A tank station
(level sensor with an inlet flow meter) runs TankBalance. Because a pipeline may hold
a record back for a few samples, the station keeps two small fixed buffers: flow
records wait until the level sample that closes their balance interval, and a level
record waits until the flows up to its time have been released. A failed balance
sets CROSSCHECK on the level record and on the flow records of that interval without
reconstructing either, because the station cannot tell which instrument is wrong.

v0.2 adds the cross-sensor model tests (xmodel.py) on every released record of a
sensor that has a model: an instantaneous test of the standardised residual against
a conformal threshold, and a two-sided CUSUM on the same residual. A failed test
alone does not say which instrument is wrong, because a bias on one sensor also
spoils the prediction of the other. Two isolation rules decide:

* CUSUM (structured residuals): a sensor whose cross-model CUSUM is in alarm while
  the CUSUMs of its inputs are not is flagged. A bias on sensor A moves A's residual
  by the bias and B's residual only through B's sensitivity to A, so the signature
  points at A; if both are in alarm nothing is flagged.
* Instantaneous test: the sensor is flagged only when it has evidence of its own, a
  level shift or a drift flag (MODEL_RESIDUAL without reconstruction), between
  ``tie_s`` and ``blame_s`` ago, and no input sensor has evidence within ``tie_s`` of
  it. A pump switching moves both sensors together, so their evidence coincides and
  nothing is flagged.

Flagged records get CROSSCHECK and keep their value.
"""

from . import quality
from .crosscheck import TankBalance
from .cusum import Cusum
from .xmodel import CrossModel, Recent

NOT_INPUT = quality.MISSING | quality.REJECTING | quality.CROSSCHECK | quality.RECONSTRUCTED


class Station:
    def __init__(self, station_id, pipelines, tank=None, hold=12, xmodels=None):
        """``pipelines``: dict sensor id -> SensorPipeline. ``tank``: optional dict with
        keys level, flow (sensor ids), area_m2, tol_m and sign (balance parameters).
        ``xmodels``: optional dict sensor id -> cross-sensor model spec."""
        self.id = station_id
        self.pipelines = pipelines
        self.tank = tank
        self.hold = hold
        self.balance = None
        self.flows = []
        self.levels = []
        self.flow_t = None
        if tank is not None:
            self.balance = TankBalance(tank["area_m2"], tank["tol_m"], tank.get("sign", 1.0))
        self.xmodels = {}
        self.recent = {}
        self.evidence = {}
        self.xcus = {}
        self.blame_s = 259200
        self.tie_s = 1800
        self.xtrace = None
        if xmodels:
            for sid in xmodels:
                spec = xmodels[sid]
                self.xmodels[sid] = CrossModel(spec)
                cs = spec.get("cus")
                if cs:
                    self.xcus[sid] = Cusum(cs[0], cs[1])
                self.blame_s = spec.get("blame_s", self.blame_s)
                self.tie_s = spec.get("tie_s", self.tie_s)
            for sid in pipelines:
                self.recent[sid] = Recent()

    def process(self, sensor, t, x):
        """Return records ``(sensor, t, raw, value, quality, score)`` released by this sample."""
        out = self._route(sensor, self.pipelines[sensor].process(t, x))
        if self.xmodels:
            return self._xcheck(out)
        return out

    def _xcheck(self, recs):
        out = []
        for r in recs:
            sensor, t, value, q = r[0], r[1], r[3], r[4]
            if q & quality.SHIFT or (q & quality.MODEL_RESIDUAL and not q & quality.RECONSTRUCTED):
                ev = self.evidence.get(sensor)
                if ev is None:
                    ev = []
                    self.evidence[sensor] = ev
                if not ev or t - ev[-1] > self.tie_s:
                    ev.append(t)
                    if len(ev) > 4:
                        ev.pop(0)
            m = self.xmodels.get(sensor)
            if m is not None and value is not None and not q & (NOT_INPUT & ~quality.CROSSCHECK):
                if self._xfails(sensor, m, t, value):
                    q = q | quality.CROSSCHECK
                    r = (sensor, t, r[2], value, q, r[5])
            if value is not None and not q & NOT_INPUT:
                self.recent[sensor].push(t, value)
            out.append(r)
        return out

    def _xfails(self, sensor, m, t, y):
        vals = []
        prevs = []
        for o in m.inputs:
            vp = self.recent[o].at(t, m.max_age)
            if vp is None:
                return False
            vals.append(vp[0])
            prevs.append(vp[1])
        z = m.residual(y, vals, prevs, t)
        if self.xtrace is not None:
            self.xtrace.append((sensor, t, z))
        c = self.xcus.get(sensor)
        if c is not None and c.update(z):
            alone = True
            for o in m.inputs:
                co = self.xcus.get(o)
                if co is not None and co.alarm():
                    alone = False
            if alone:
                return True
        if abs(z) <= m.q:
            return False
        return self._blame(sensor, m.inputs, t)

    def _blame(self, sensor, inputs, t):
        """True when the sensor's own latest evidence explains the failure."""
        own = self.evidence.get(sensor)
        if not own:
            return False
        e = own[-1]
        if t - e < self.tie_s or t - e > self.blame_s:
            return False
        for o in inputs:
            for eo in self.evidence.get(o, ()):
                if abs(eo - e) <= self.tie_s:
                    return False
        return True

    def _route(self, sensor, recs):
        if self.balance is None:
            return [(sensor,) + r for r in recs]
        out = []
        for r in recs:
            rec = [sensor, r[0], r[1], r[2], r[3], r[4]]
            if sensor == self.tank["flow"]:
                out.extend(self._close_before(rec[1]))
                if rec[3] is not None:
                    self.balance.flow(rec[1], rec[3])
                self.flow_t = rec[1]
                self.flows.append(rec)
                out.extend(self._close_before(rec[1] + 1))
                if len(self.flows) > self.hold:
                    out.append(tuple(self.flows.pop(0)))
            elif sensor == self.tank["level"]:
                self.levels.append(rec)
                flow_pipe = self.pipelines[self.tank["flow"]]
                if not flow_pipe.pending and self.flow_t is not None and self.flow_t >= rec[1]:
                    out.extend(self._close_before(rec[1] + 1))
                while len(self.levels) > 3:
                    out.extend(self._close(self.levels[0]))
            else:
                out.append(tuple(rec))
        return out

    def _close_before(self, t):
        out = []
        while self.levels and self.levels[0][1] < t:
            out.extend(self._close(self.levels[0]))
        return out

    def _close(self, rec):
        self.levels.pop(0)
        t = rec[1]
        if rec[3] is not None and not quality.is_suspect(rec[4]) and not rec[4] & quality.SHIFT:
            if not self.balance.level(t, rec[3]):
                rec[4] = rec[4] | quality.CROSSCHECK
                for fr in self.flows:
                    if fr[1] <= t:
                        fr[4] = fr[4] | quality.CROSSCHECK
        else:
            self.balance.last_level = None
            self.balance.volume = 0.0
        out = [tuple(fr) for fr in self.flows if fr[1] <= t]
        self.flows = [fr for fr in self.flows if fr[1] > t]
        out.append(tuple(rec))
        return out

    def flush(self):
        """Release everything held: samples held by the pipelines, then the station buffers."""
        out = []
        for sensor in sorted(self.pipelines):
            out.extend(self._route(sensor, self.pipelines[sensor].flush()))
        while self.levels:
            out.extend(self._close(self.levels[0]))
        out.extend(tuple(fr) for fr in self.flows)
        self.flows = []
        if self.xmodels:
            return self._xcheck(out)
        return out
