"""A station: several sensor pipelines plus the station-level cross-check.

Samples must be offered in time order across the station's sensors. A tank station
(level sensor with an inlet flow meter) runs TankBalance. Because a pipeline may hold
a record back for a few samples, the station keeps two small fixed buffers: flow
records wait until the level sample that closes their balance interval, and a level
record waits until the flows up to its time have been released. A failed balance
sets CROSSCHECK on the level record and on the flow records of that interval without
reconstructing either, because the station cannot tell which instrument is wrong.
"""

from . import quality
from .crosscheck import TankBalance


class Station:
    def __init__(self, station_id, pipelines, tank=None, hold=12):
        """``pipelines``: dict sensor id -> SensorPipeline. ``tank``: optional dict with
        keys level, flow (sensor ids), area_m2, tol_m and sign (balance parameters)."""
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

    def process(self, sensor, t, x):
        """Return records ``(sensor, t, raw, value, quality, score)`` released by this sample."""
        return self._route(sensor, self.pipelines[sensor].process(t, x))

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
        return out
