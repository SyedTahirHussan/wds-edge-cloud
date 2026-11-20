"""Model-based validation level (P2.2) and chlorine decay calibration (E10) helpers."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from flowmine.experiments import chlorine_cal, model_validation


def test_conformal_q_is_the_finite_sample_rank() -> None:
    s = np.arange(1.0, 101.0)  # 100 scores
    assert model_validation.conformal_q(s, 0.05) == 96.0  # ceil(101 * 0.95) = 96
    assert model_validation.conformal_q(np.append(s, np.nan), 0.05) == 96.0
    assert math.isinf(model_validation.conformal_q(s[:10], 0.05))


def test_common_mode_removal_cancels_a_shared_offset() -> None:
    idx = pd.Index([0, 300, 600], name="time_s")
    res = pd.DataFrame({"P-1": [0.1, 2.1, 0.1], "P-2": [0.0, 2.0, 0.0], "P-3": [-0.1, 1.9, 3.0]})
    res.index = idx
    cm = res.sub(res.median(axis=1), axis=0)
    assert np.allclose(cm.loc[300], [0.1, 0.0, -0.1])
    assert cm.loc[600, "P-3"] > 2.5  # a single-sensor fault stays visible


def test_interp_weights_reproduce_linear_interpolation() -> None:
    fine = np.array([0.0, -0.1, -0.55, -2.2, -3.0])
    w = chlorine_cal.interp_weights(fine)
    grid = np.array(chlorine_cal.KB_GRID)
    values = np.exp(0.7 * grid)  # any smooth curve over the grid
    order = np.argsort(grid)
    expect = np.interp(fine, grid[order], values[order])
    assert np.allclose(values @ w, expect)
    assert np.allclose(w.sum(axis=0), 1.0)


def test_fit_recovers_the_coefficient_of_noise_free_data() -> None:
    grid = np.array(chlorine_cal.KB_GRID)
    age_days = np.array([0.2, 0.5, 1.0, 1.5, 2.5])
    pred = np.exp(np.outer(age_days, grid))  # c = exp(kb * age), one row per sample
    fine = np.round(np.arange(0.0, -3.0001, -0.01), 2)
    w = chlorine_cal.interp_weights(fine)
    y = np.exp(-0.8 * age_days)
    assert abs(chlorine_cal.fit(pred, y, fine, w) - (-0.8)) <= 0.02
