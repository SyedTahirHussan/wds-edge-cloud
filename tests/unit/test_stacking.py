from __future__ import annotations

import numpy as np

from flowmine.hybrid import stacking


def test_budget_threshold_keeps_rate_at_or_below_budget() -> None:
    rng = np.random.default_rng(0)
    s = rng.random(1000)
    for b in (0.005, 0.01, 0.02):
        t = stacking.budget_threshold(s, b)
        assert float((s > t).mean()) <= b
        # one step lower would exceed the budget
        assert float((s >= t).mean()) > b - 1e-12
    assert stacking.budget_threshold(np.array([]), 0.01) == -np.inf


def test_stacked_fit_flags_and_coefficients() -> None:
    rng = np.random.default_rng(1)
    n = 4000
    x = rng.normal(size=(n, len(stacking.FEATURES)))
    y = x[:, 8] + 0.5 * rng.normal(size=n) > 2.0  # spatial_ratio carries the signal
    clean = ~y
    m = stacking.Stacked.fit(x, y, clean, (0.01,), seed=1)
    flags = m.flags(x, 0.01)
    assert float(flags[clean].mean()) <= 0.01
    assert float(flags[y].mean()) > 0.3
    coef = m.coefficients().set_index("feature").coef
    assert len(coef) == len(stacking.FEATURES) + 1
    assert coef["spatial_ratio"] == coef.drop("intercept").abs().max()
