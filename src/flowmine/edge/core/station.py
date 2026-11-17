"""A station: several sensor pipelines plus the station-level cross-check.

Samples must be offered in time order across the station's sensors. A tank station
(level sensor with an inlet flow meter) runs TankBalance. Flow records are held
until the next level sample closes the balance interval (at most ``hold`` records,
a fixed buffer); a failed balance sets CROSSCHECK on the level record and on the
held flow records without reconstructing either, because the station cannot tell
which of the two instruments is wrong.
"""

from . import quality
from .crosscheck import TankBalance


class Station:
    def __init__(self, station_id, pipelines, tank=None, hold=12):
        """``pipelines``: dict sensor id -> SensorPipeline. ``tank``: optional dict with
        keys level, flow (sensor ids), area_m2 and tol_m (balance parameters)."""
        self.id = station_id
        self.pipelines = pipelines
        self.tank = tank
        self.hold = hold
        self.balance = None
        self.pending = []
        if tank is not None:
            self.balance = TankBalance(tank["area_m2"], tank["tol_m"])

    def process(self, sensor, t, x):
        """Return records ``(sensor, t, raw, value, quality, score)`` released by this sample."""
        recs = self.pipelines[sensor].process(t, x)
        if self.balance is None:
            return [(sensor,) + r for r in recs]
        out = []
        for r in recs:
            rec = [sensor, r[0], r[1], r[2], r[3], r[4]]
            if sensor == self.tank["flow"]:
                if r[2] is not None:
                    self.balance.flow(r[0], r[2])
                self.pending.append(rec)
                if len(self.pending) > self.hold:
                    out.append(tuple(self.pending.pop(0)))
            elif sensor == self.tank["level"]:
                if r[2] is not None and not quality.is_suspect(r[3]):
                    if not self.balance.level(r[0], r[2]):
                        rec[4] = rec[4] | quality.CROSSCHECK
                        for fr in self.pending:
                            fr[4] = fr[4] | quality.CROSSCHECK
                else:
                    self.balance.last_level = None
                    self.balance.volume = 0.0
                out.extend(tuple(fr) for fr in self.pending)
                self.pending = []
                out.append(tuple(rec))
            else:
                out.append(tuple(rec))
        return out

    def flush(self):
        out = [tuple(fr) for fr in self.pending]
        self.pending = []
        return out
