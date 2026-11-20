"""Calibrated stacking (v0.4): a logistic model on edge and central scores with a
false-alarm budget.

The v0.1 fusion (fusion.py) takes three edge/central features and picks the threshold
that maximises F1 on week 7. Here the model sees every score the two sides produce
for a sample (see ``FEATURES`` and docs/methods.md), the features are standardised
with their week-7 mean and sd, and the operating threshold is the smallest one whose
false-alarm rate on week-7 clean samples is at or below a stated budget:

    p = sigma(b + w . (x - mu) / sd),  flag when p > t_B,
    t_B = the (n - floor(B n))-th smallest p among the n clean week-7 samples.

Missing values (no forecast, no cross-sensor model, no spatial or twin score for the
sensor) are set to 0 and get their own 0/1 indicator. Nothing is tuned on week 8.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

KINDS = ("pressure", "flow", "level", "chlorine")
FEATURES = (
    "edge_reject",
    "edge_shift",
    "edge_score",
    "edge_score_missing",
    "cross_abs_z",
    "cross_missing",
    "cusum_ratio",
    "cusum_missing",
    "spatial_ratio",
    "spatial_missing",
    "model_ratio",
    "model_missing",
    "lab_drift",
    *(f"kind_{k}" for k in KINDS),
    "maintenance_recent",
)
CLIP = 10.0


def budget_threshold(clean_scores: np.ndarray, budget: float) -> float:
    """Smallest t with mean(clean_scores > t) <= budget."""
    s = np.sort(np.asarray(clean_scores, dtype=float))
    n = len(s)
    k = math.floor(budget * n)
    if n == 0 or k >= n:
        return -math.inf
    return float(s[n - k - 1])


@dataclass
class Stacked:
    model: LogisticRegression
    mu: np.ndarray
    sd: np.ndarray
    thresholds: dict[float, float] = field(default_factory=dict)

    @classmethod
    def fit(
        cls,
        x: np.ndarray,
        y: np.ndarray,
        clean: np.ndarray,
        budgets: tuple[float, ...],
        seed: int,
        c: float = 1.0,
    ) -> Stacked:
        mu = x.mean(axis=0)
        sd = x.std(axis=0)
        sd[sd == 0] = 1.0
        m = LogisticRegression(C=c, class_weight="balanced", random_state=seed, max_iter=2000)
        m.fit((x - mu) / sd, y)
        out = cls(m, mu, sd)
        p = out.proba(x)
        out.thresholds = {b: budget_threshold(p[clean], b) for b in budgets}
        return out

    def proba(self, x: np.ndarray) -> np.ndarray:
        return np.asarray(self.model.predict_proba((x - self.mu) / self.sd)[:, 1])

    def flags(self, x: np.ndarray, budget: float) -> np.ndarray:
        return self.proba(x) > self.thresholds[budget]

    def coefficients(self) -> pd.DataFrame:
        """Standardised coefficients (per sd of the feature) and the intercept."""
        names = [*FEATURES, "intercept"]
        vals = [*map(float, self.model.coef_[0]), float(self.model.intercept_[0])]
        return pd.DataFrame({"feature": names, "coef": vals})


def matrix(f: pd.DataFrame) -> np.ndarray:
    """Feature matrix in the order of FEATURES from a frame holding those columns."""
    return f[list(FEATURES)].to_numpy(dtype=float)
