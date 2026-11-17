"""Homogenisation: SensorThings observations onto one time grid, with quality attached.

Streams have different periods (5 min for pressure and flow, 15 min for level and
chlorine). On the common grid a slower stream's value is carried forward until its
next observation, at most ``hold`` grid steps, so a value is never used before its
phenomenon time. Laboratory results are aligned on their phenomenon time but are
only *usable* from their result time; ``lab_available`` enforces that.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from flowmine.edge.core import quality


@dataclass
class Grid:
    """Wide arrays on a common grid: values, quality codes and an "unusable" mask."""

    times: np.ndarray  # seconds, shape (n,)
    streams: list[str]
    values: np.ndarray  # (n, m), NaN where nothing is known
    quality: np.ndarray  # (n, m) int, edge quality code (0 for raw streams)
    suspect: np.ndarray  # (n, m) bool, the edge marked it missing/rejected/cross-check

    def col(self, stream: str) -> int:
        return self.streams.index(stream)

    def frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.values, index=self.times, columns=self.streams)


def align(
    frame: pd.DataFrame, streams: list[str], t0: int, t1: int, step: int, periods: dict[str, int]
) -> Grid:
    """Place a tidy observation frame (stream, time_s, value, quality) on [t0, t1)."""
    times = np.arange(t0, t1, step)
    n, m = len(times), len(streams)
    values = np.full((n, m), np.nan)
    codes = np.zeros((n, m), dtype=np.int64)
    sub = frame[frame.stream.isin(streams) & (frame.time_s >= t0) & (frame.time_s < t1)]
    for j, s in enumerate(streams):
        f = sub[sub.stream == s]
        if f.empty:
            continue
        idx = ((f.time_s.to_numpy() - t0) // step).astype(int)
        v = pd.to_numeric(f.value, errors="coerce").to_numpy(dtype=float)
        values[idx, j] = v
        codes[idx, j] = f.quality.to_numpy(dtype=np.int64)
        hold = max(periods.get(s, step) // step - 1, 0)
        if hold:
            col = pd.Series(values[:, j])
            qcol = pd.Series(np.where(np.isnan(values[:, j]), np.nan, codes[:, j]))
            values[:, j] = col.ffill(limit=hold).to_numpy()
            codes[:, j] = qcol.ffill(limit=hold).fillna(0).to_numpy().astype(np.int64)
    bad = (codes & (quality.MISSING | quality.REJECTING | quality.CROSSCHECK)) != 0
    suspect = bad | np.isnan(values)
    return Grid(times, list(streams), values, codes, suspect)


def raw_frame(observed: pd.DataFrame, t0: int, t1: int) -> pd.DataFrame:
    """Tidy frame of raw, unvalidated observations (what the ``raw`` policy delivers)."""
    o = observed[(observed.time_s >= t0) & (observed.time_s < t1) & observed.value.notna()]
    return pd.DataFrame(
        {
            "stream": o.sensor_id.to_numpy(),
            "time_s": o.time_s.to_numpy(),
            "result_s": o.time_s.to_numpy(),
            "value": o.value.to_numpy(),
            "quality": 0,
            "model_version": None,
            "reconstructed": False,
        }
    )


def lab_available(lab: pd.DataFrame, now_s: int) -> pd.DataFrame:
    """Laboratory results the cloud holds at ``now_s`` (reported, not just sampled)."""
    return lab[lab.result_s <= now_s]
