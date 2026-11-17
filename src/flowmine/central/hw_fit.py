"""Cloud-side fitting of the edge Holt-Winters forecaster.

``reference_forecasts`` is a direct numpy transcription of the equations in
``flowmine.edge.core.holtwinters`` (float64); tests compare the two. ``fit`` picks the
smoothing constants by a grid search on one-step error over the second half of the
training data, sets phi from the lag-1 autocorrelation of the remaining error, and
returns the edge state at the end of training, ready to be loaded on the station.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

import numpy as np

from flowmine.edge.core.holtwinters import HoltWinters

DAY = 86400


@dataclass(frozen=True)
class HWState:
    level: float
    e: float
    d: np.ndarray
    w: np.ndarray
    k: int


@dataclass(frozen=True)
class HWParams:
    alpha: float
    delta: float
    omega: float
    phi: float


def seasons(period_s: int) -> tuple[int, int]:
    s1 = DAY // period_s
    return s1, 7 * s1


def initial_state(y: np.ndarray, s1: int, s2: int) -> tuple[float, np.ndarray, np.ndarray]:
    """Level, daily and weekly indices from the first week (NaNs ignored)."""
    first = y[:s2]
    level = float(np.nanmean(first))
    days = first[: (len(first) // s1) * s1].reshape(-1, s1) - level
    d = np.nan_to_num(np.nanmean(days, axis=0))
    w = np.zeros(s2)
    k = np.arange(len(first))
    w[: len(first)] = np.nan_to_num(first - level - d[k % s1])
    return level, d, w


def reference_forecasts(
    y: np.ndarray, p: HWParams, s1: int, s2: int, init: tuple[float, np.ndarray, np.ndarray]
) -> tuple[np.ndarray, HWState]:
    """One-step forecasts for y[1:], starting from ``init`` at slot -1, plus the final state."""
    level, d0, w0 = init
    d, w = d0.astype(float).copy(), w0.astype(float).copy()
    e = 0.0
    out = np.empty(len(y))
    for k, x in enumerate(y):
        ds, ws = d[k % s1], w[k % s2]
        out[k] = level + ds + ws + p.phi * e
        if np.isnan(x):
            e = p.phi * e
            continue
        e = x - (level + ds + ws)
        lv = p.alpha * (x - ds - ws) + (1 - p.alpha) * level
        d[k % s1] = p.delta * (x - lv - ws) + (1 - p.delta) * ds
        w[k % s2] = p.omega * (x - lv - ds) + (1 - p.omega) * ws
        level = lv
    return out, HWState(float(level), float(e), d, w, len(y) - 1)


def _sse(
    y: list[float],
    a: float,
    dl: float,
    om: float,
    s1: int,
    s2: int,
    init: tuple[float, np.ndarray, np.ndarray],
    start: int,
) -> tuple[float, list[float]]:
    """Scalar-loop error of the trend-free recursion without the AR term (fast path)."""
    level, d0, w0 = init
    d, w = d0.tolist(), w0.tolist()
    sse = 0.0
    errs: list[float] = []
    for k, x in enumerate(y):
        i1, i2 = k % s1, k % s2
        ds, ws = d[i1], w[i2]
        if x != x:
            if k >= start:
                errs.append(0.0)
            continue
        err = x - level - ds - ws
        if k >= start:
            sse += err * err
            errs.append(err)
        lv = a * (x - ds - ws) + (1 - a) * level
        d[i1] = dl * (x - lv - ws) + (1 - dl) * ds
        w[i2] = om * (x - lv - ds) + (1 - om) * ws
        level = lv
    return sse, errs


def fit(
    y: np.ndarray, period_s: int, grid: tuple[float, ...], phi_grid: tuple[float, ...]
) -> tuple[HWParams, HoltWinters]:
    """Fit on training series ``y`` (slot 0 = Monday 00:00) and return params + edge model."""
    s1, s2 = seasons(period_s)
    init = initial_state(y, s1, s2)
    ys = y.tolist()
    start = len(y) // 2
    best: tuple[float, float, float, float, list[float]] | None = None
    for a, dl, om in itertools.product(grid, grid, grid):
        sse, errs = _sse(ys, a, dl, om, s1, s2, init, start)
        if best is None or sse < best[0]:
            best = (sse, a, dl, om, errs)
    assert best is not None
    _, a, dl, om, errs = best
    ev = np.asarray(errs)
    rho = float(np.corrcoef(ev[:-1], ev[1:])[0, 1]) if ev.std() > 0 else 0.0
    phi = min(phi_grid, key=lambda f: abs(f - max(rho, 0.0)))
    params = HWParams(a, dl, om, phi)
    _, st = reference_forecasts(y, params, s1, s2, init)
    model = HoltWinters(period_s, s1, s2, a, dl, om, phi, t0=0)
    model.load(
        {
            "period": period_s,
            "s1": s1,
            "s2": s2,
            "alpha": a,
            "delta": dl,
            "omega": om,
            "phi": phi,
            "t0": 0,
            "k": st.k,
            "level": st.level,
            "e": st.e,
            "d": st.d.tolist(),
            "w": st.w.tolist(),
        }
    )
    return params, model


def residual_scale(y: np.ndarray, params: HWParams, period_s: int) -> float:
    """Mean absolute one-step error over the second half of training (initial threshold scale)."""
    s1, s2 = seasons(period_s)
    f, _ = reference_forecasts(y, params, s1, s2, initial_state(y, s1, s2))
    r = (y - f)[len(y) // 2 :]
    return float(np.nanmean(np.abs(r)))
