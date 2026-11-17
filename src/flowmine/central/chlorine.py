"""Supervision of on-line chlorine analysers with laboratory grab samples.

For each grab sample j the cloud forms d_j = analyser(t_j) - lab_j, using the
analyser value at the sampling time; the difference only becomes known at the
laboratory's result time. A two-sided tabular CUSUM on (d_j - mu0) / sigma, with
mu0 and sigma from the training weeks, raises a drift alarm when either sum exceeds
``h`` (allowance ``k``). The offset estimate is the mean of (d - mu0) over the samples
since the alarming sum last left zero, and the proposed correction subtracts it. A
recalibration or replacement record in the maintenance log resets the sums: after
it, the analyser is trusted again. This is a simplified form of the on-line/off-line
combination described by Pérez et al. (2022), using a generic sequential test.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

RESET_TYPES = ("sensor_recalibration", "sensor_replacement")


@dataclass
class DriftAlarm:
    sensor_id: str
    sample_s: int  # phenomenon time of the grab sample that triggered the alarm
    alarm_s: int  # when the cloud knows (laboratory result time)
    offset: float  # estimated analyser offset (mg/L)


def differences(lab: pd.DataFrame, analyser: pd.DataFrame) -> pd.DataFrame:
    """Join grab samples with the analyser value at their sampling time.

    ``analyser``: tidy frame (stream, time_s, value) of one or more analysers.
    """
    a = analyser[["stream", "time_s", "value"]].rename(columns={"stream": "sensor_id"})
    a = a.sort_values("time_s")
    out = []
    for sid, g in lab.groupby("sensor_id"):
        ag = a[a.sensor_id == sid]
        if ag.empty:
            continue
        m = pd.merge_asof(
            g.sort_values("phenomenon_s"),
            ag[["time_s", "value"]],
            left_on="phenomenon_s",
            right_on="time_s",
            direction="backward",
            tolerance=900,
            suffixes=("_lab", "_an"),
        )
        out.append(m.assign(sensor_id=sid))
    if not out:
        return pd.DataFrame(columns=["sensor_id", "phenomenon_s", "result_s", "d"])
    df = pd.concat(out, ignore_index=True)
    df["d"] = pd.to_numeric(df.value_an, errors="coerce") - df.value_lab
    return df[["sensor_id", "phenomenon_s", "result_s", "d"]].dropna()


def baseline(diffs: pd.DataFrame, min_sd: float) -> dict[str, tuple[float, float]]:
    return {
        str(sid): (float(g.d.mean()), max(float(g.d.std()), min_sd))
        for sid, g in diffs.groupby("sensor_id")
    }


def cusum(
    diffs: pd.DataFrame,
    base: dict[str, tuple[float, float]],
    k: float,
    h: float,
    maintenance: pd.DataFrame | None = None,
) -> list[DriftAlarm]:
    """Run the CUSUM in result-time order; returns one alarm per crossing."""
    alarms = []
    for key, g in diffs.groupby("sensor_id"):
        sid = str(key)
        mu, sd = base.get(sid, (0.0, 1.0))
        resets = np.array([], dtype=float)
        if maintenance is not None:
            mm = maintenance[(maintenance.asset == sid) & maintenance.type.isin(RESET_TYPES)]
            resets = np.sort(mm.time_s.to_numpy(dtype=float))
        sp = sn = 0.0
        run_p: list[float] = []
        run_n: list[float] = []
        last_t = -np.inf
        for _, r in g.sort_values("result_s").iterrows():
            if ((resets > last_t) & (resets <= r.phenomenon_s)).any():
                sp = sn = 0.0
                run_p, run_n = [], []
            last_t = r.phenomenon_s
            e = (r.d - mu) / sd
            sp = max(0.0, sp + e - k)
            sn = max(0.0, sn - e - k)
            run_p = [*run_p, r.d - mu] if sp > 0 else []
            run_n = [*run_n, r.d - mu] if sn > 0 else []
            if sp > h or sn > h:
                run = run_p if sp > h else run_n
                alarms.append(
                    DriftAlarm(sid, int(r.phenomenon_s), int(r.result_s), float(np.mean(run)))
                )
                sp = sn = 0.0
                run_p, run_n = [], []
    return alarms


def corrected(
    values: pd.Series, times: np.ndarray, alarms: list[DriftAlarm], resets: np.ndarray
) -> pd.Series:
    """Apply proposed corrections from each alarm until the next reset record."""
    out = values.copy()
    for a in alarms:
        later = resets[resets > a.sample_s]
        end = later.min() if len(later) else np.inf
        sel = (times >= a.alarm_s) & (times < end)
        out[sel] = out[sel] - a.offset
    return out
