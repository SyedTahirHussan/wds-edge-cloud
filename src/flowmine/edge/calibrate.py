"""Week-7 calibration of the edge residual threshold, per sensor.

The station first runs over the calibration week with the default multiplier
``residual_k``. On samples that carry no fault and no process-event label, the
residual score (|r| over the threshold) is collected, leaving out samples released as
level shifts (those are not rejections); the multiplier is then scaled
so that a fraction ``calib_false_alarm`` of those clean scores would exceed it.
The multiplier never drops below the default. The labels of the calibration week
are used only here and in the central thresholds, never on the test week.
"""

from __future__ import annotations

import copy

import numpy as np
import pandas as pd

from flowmine.config import Config
from flowmine.edge import replay
from flowmine.edge.core import quality
from flowmine.edge.models import SensorModels
from flowmine.testbed.build import Testbed


def calibrate(
    tb: Testbed, bundles: dict[str, SensorModels], cfg: Config, forecaster: str = "hw"
) -> dict[str, SensorModels]:
    t0, t1 = tb.periods["calib"]
    rows = []
    for spec in tb.stations:
        node = replay.run_station(
            replay.make_node(tb, spec, bundles, "validated", forecaster), tb, t0, t1
        )
        rows.extend(node.records)
    df = pd.DataFrame(rows, columns=["sensor_id", "time_s", "raw", "value", "q", "score"])
    lab = tb.labels
    df = df.merge(lab, on=["sensor_id", "time_s"], how="left")
    shift = (df.q & quality.SHIFT) != 0
    clean = df[(df.fault_type == "") & (df.event_id < 0) & (df.score > 0) & ~shift]
    out = {}
    base_k = cfg.edge.residual_k
    for sid, b in bundles.items():
        sc = clean.loc[clean.sensor_id == sid, "score"].to_numpy()
        factor = float(np.quantile(sc, 1.0 - cfg.edge.calib_false_alarm)) if len(sc) > 20 else 1.0
        nb = copy.copy(b)
        nb.meta = {**b.meta, "k": base_k * max(1.0, factor)}
        out[sid] = nb
    return out
