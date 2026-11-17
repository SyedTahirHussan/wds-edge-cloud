"""External data: air temperature as a covariate of the total-demand forecast.

The total demand is the water balance of the metered inflows and tank inflows,
averaged per hour. The day-ahead forecaster is a ridge regression on hour-of-week
indicators plus the demand of the same hour one day earlier; the second variant adds
the hourly air temperature (taken as known, i.e. a perfect weather forecast, which
favours the covariate). Both are fitted on the training weeks and scored on the test
week.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class DemandForecastScore:
    mae_without: float
    mae_with: float
    mean_demand: float


def hourly_total_demand(
    wide: pd.DataFrame, sources: list[str], inlets: dict[str, float]
) -> pd.Series:
    """Hourly mean of sum(sources) - sum(sign * inlet) from a time_s x stream frame (L/s)."""
    d = wide[sources].sum(axis=1, min_count=len(sources))
    for s, sign in inlets.items():
        d = d - sign * wide[s]
    hours = (wide.index.to_numpy() // 3600).astype(int)
    return d.groupby(hours).mean()


def _design(hours: np.ndarray, lag: np.ndarray, temp: np.ndarray | None) -> np.ndarray:
    how = np.zeros((len(hours), 168))
    how[np.arange(len(hours)), hours % 168] = 1.0
    cols = [how, lag[:, None]]
    if temp is not None:
        cols.append(temp[:, None])
    return np.hstack(cols)


def score(
    demand: pd.Series, temp: pd.Series, train_end_h: int, test: tuple[int, int], alpha: float = 1.0
) -> DemandForecastScore:
    h = demand.index.to_numpy()
    y = demand.to_numpy()
    lag = pd.Series(y, index=h).shift(24).to_numpy()
    tt = temp.reindex(h).to_numpy()
    ok = np.isfinite(y) & np.isfinite(lag) & np.isfinite(tt)
    tr = ok & (h < train_end_h)
    te = ok & (h >= test[0]) & (h < test[1])
    out = []
    for use_temp in (False, True):
        x = _design(h, lag, tt if use_temp else None)
        mu, sd = x[tr].mean(axis=0), x[tr].std(axis=0)
        sd[sd == 0] = 1.0
        xs = (x - mu) / sd
        a = xs[tr].T @ xs[tr] + alpha * np.eye(xs.shape[1])
        coef = np.linalg.solve(a, xs[tr].T @ (y[tr] - y[tr].mean()))
        pred = xs[te] @ coef + y[tr].mean()
        out.append(float(np.mean(np.abs(pred - y[te]))))
    return DemandForecastScore(out[0], out[1], float(np.mean(y[te])))
