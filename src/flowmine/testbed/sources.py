"""Heterogeneous non-SCADA sources generated from the same simulation.

* laboratory chlorine grab samples (more accurate than the on-line analyser,
  reported 24-48 h after sampling);
* a maintenance log with free-text notes;
* the hourly air temperature that drives demand.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from flowmine.config import Config
from flowmine.testbed.faults import FaultEvent
from flowmine.testbed.simulate import ProcessEvent

DAY = 86400


def lab_samples(
    rng: np.random.Generator,
    chlorine_truth: pd.DataFrame,
    chlorine_sensors: dict[str, str],
    n_days: int,
    cfg: Config,
) -> pd.DataFrame:
    """One grab sample per sampling point per working day.

    ``chlorine_truth``: true chlorine (mg/L) at 5-minute steps, columns = node ids;
    ``chlorine_sensors``: node id -> sensor id of the on-line analyser at that node.
    """
    lc = cfg.lab
    step = cfg.network.hydraulic_step_s
    rows = []
    sid = 0
    for day in range(n_days):
        if day % 7 >= 5:
            continue
        for node, sensor in sorted(chlorine_sensors.items()):
            t = day * DAY + int(rng.uniform(*lc.hour_range) * 3600) // step * step
            truth = float(chlorine_truth[node].iloc[t // step])
            value = round((truth + rng.normal(0, lc.noise_sd)) / lc.resolution) * lc.resolution
            delay = int(rng.uniform(*lc.delay_h) * 3600)
            rows.append(
                {
                    "sample_id": f"LAB-{sid:05d}",
                    "node": node,
                    "sensor_id": sensor,
                    "phenomenon_s": t,
                    "result_s": t + delay,
                    "value": max(value, 0.0),
                }
            )
            sid += 1
    return pd.DataFrame(rows)


def maintenance_log(
    rng: np.random.Generator,
    faults: list[FaultEvent],
    events: list[ProcessEvent],
    sensor_periods: dict[str, int],
    all_sensors: list[str],
    periods: dict[str, tuple[int, int]],
) -> pd.DataFrame:
    """Records implied by the faults and operations, plus routine recalibrations."""
    rows = []
    for f in faults:
        per = sensor_periods[f.sensor_id]
        end = (f.start_idx + f.n) * per
        if f.fault_type == "drift":
            rows.append(
                {
                    "time_s": end,
                    "asset": f.sensor_id,
                    "type": "sensor_recalibration",
                    "note": "analyser recalibrated against reference; "
                    f"offset found {f.magnitude:+.3f}",
                }
            )
        elif f.fault_type == "stuck" and f.n * per >= 4 * 3600:
            rows.append(
                {
                    "time_s": end,
                    "asset": f.sensor_id,
                    "type": "sensor_replacement",
                    "note": "sensor reported frozen value; transmitter replaced",
                }
            )
    for e in events:
        hours = (e.end_s - e.start_s) / 3600
        if e.event_type == "valve_operation":
            rows.append(
                {
                    "time_s": e.start_s,
                    "asset": e.element,
                    "type": "valve_operation",
                    "note": f"isolation valve on pipe {e.element} closed for works, {hours:.1f} h",
                }
            )
        elif e.event_type == "pump_maintenance":
            rows.append(
                {
                    "time_s": e.start_s,
                    "asset": e.element,
                    "type": "pump_maintenance",
                    "note": f"pump {e.element} out of service for maintenance, {hours:.1f} h",
                }
            )
    for t0, t1 in periods.values():
        for _ in range(2):
            rows.append(
                {
                    "time_s": int(rng.uniform(t0, t1)) // 300 * 300,
                    "asset": str(rng.choice(all_sensors)),
                    "type": "sensor_recalibration",
                    "note": "routine calibration check, no adjustment needed",
                }
            )
    return pd.DataFrame(rows).sort_values(["time_s", "asset"]).reset_index(drop=True)


def weather_frame(temp_hourly: np.ndarray) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "time_s": np.arange(len(temp_hourly)) * 3600,
            "air_temperature_c": np.round(temp_hourly, 2),
        }
    )
