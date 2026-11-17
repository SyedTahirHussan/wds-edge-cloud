"""CPython-side tests of the edge core: the shared suite plus numpy reference checks."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from flowmine.central import hw_fit, nn
from flowmine.edge.core import mlp as core_mlp
from flowmine.edge.core.dualpred import DeltaReceiver, DeltaSender
from flowmine.edge.core.holtwinters import HoltWinters
from flowmine.edge.core.snaive import SeasonalNaive

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "micropython"))
import core_tests

SHARED = sorted(n for n in dir(core_tests) if n.startswith("test_"))


@pytest.mark.parametrize("name", SHARED)
def test_shared_core_suite(name: str) -> None:
    getattr(core_tests, name)()


def test_core_suite_under_micropython() -> None:
    exe = shutil.which("micropython")
    if exe is None:
        pytest.skip("micropython not installed")
    out = subprocess.run(
        [exe, "tests/micropython/run.py"], capture_output=True, text=True, timeout=300
    )
    assert out.returncode == 0, out.stdout[-2000:]
    assert f"{len(SHARED)} passed, 0 failed" in out.stdout


def _series(n_days: int, period: int, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    s1 = 86400 // period
    k = np.arange(n_days * s1)
    daily = 3.0 * np.sin(2 * np.pi * k / s1) + np.cos(4 * np.pi * k / s1)
    weekly = 0.8 * ((k // s1) % 7 >= 5)
    return (
        40.0 + daily + weekly + np.cumsum(rng.normal(0, 0.01, len(k))) + rng.normal(0, 0.05, len(k))
    )


def test_holtwinters_matches_numpy_reference() -> None:
    period = 900
    y = _series(21, period)
    s1, s2 = hw_fit.seasons(period)
    params = hw_fit.HWParams(0.1, 0.05, 0.2, 0.6)
    init = hw_fit.initial_state(y, s1, s2)
    ref, _ = hw_fit.reference_forecasts(y, params, s1, s2, init)
    edge = HoltWinters(period, s1, s2, params.alpha, params.delta, params.omega, params.phi)
    edge.load(
        {
            "period": period,
            "s1": s1,
            "s2": s2,
            "alpha": params.alpha,
            "delta": params.delta,
            "omega": params.omega,
            "phi": params.phi,
            "t0": 0,
            "k": -1,
            "level": init[0],
            "e": 0.0,
            "d": list(init[1]),
            "w": list(init[2]),
        }
    )
    got = []
    for k, x in enumerate(y):
        got.append(edge.forecast_at(k * period))
        edge.update(None if k % 97 == 0 else float(x), k * period)
    y_gap = y.copy()
    y_gap[::97] = np.nan
    ref_gap, _ = hw_fit.reference_forecasts(y_gap, params, s1, s2, init)
    assert np.max(np.abs(np.array(got) - ref_gap)) < 1e-3
    assert np.max(np.abs(ref - ref_gap)) > 1e-3


def test_holtwinters_fit_beats_seasonal_naive() -> None:
    period = 900
    y = _series(21, period)
    params, model = hw_fit.fit(y[: 14 * 96], period, (0.05, 0.2), (0.0, 0.5, 0.9))
    sn = SeasonalNaive(period, 7 * 96)
    for k in range(14 * 96):
        sn.update(float(y[k]), k * period)
    e_hw, e_sn = [], []
    for k in range(14 * 96, 21 * 96):
        e_hw.append(model.forecast_at(k * period) - y[k])
        e_sn.append(sn.forecast_at(k * period) - y[k])
        model.update(float(y[k]), k * period)
        sn.update(float(y[k]), k * period)
    assert np.sqrt(np.mean(np.square(e_hw))) < np.sqrt(np.mean(np.square(e_sn)))
    assert 0.0 <= params.phi <= 0.9


def test_seasonal_naive_matches_reference() -> None:
    y = _series(15, 900)
    s2 = 7 * 96
    sn = SeasonalNaive(900, s2)
    got = []
    for k, x in enumerate(y):
        if k >= s2:
            got.append(sn.forecast_at(k * 900))
        sn.update(float(x), k * 900)
    assert np.allclose(got, y[: len(y) - s2].astype(np.float32), atol=1e-5)


@pytest.fixture(scope="module")
def trained_mlp() -> tuple[nn.MLP, np.ndarray]:
    period = 900
    y = _series(14, period)
    mean, std = float(y.mean()), float(y.std())
    z = (y - mean) / std
    rng = np.random.default_rng(0)
    m = nn.init_mlp(rng, 12 + 1 + 4, 16, 12, period, mean, std)
    x, t = nn.features(z, 12, m.s1, m.s2)
    before = nn.one_step_mse(m, z)
    m = nn.train(m, x, t, 15, 0.003, 128, rng)
    assert nn.one_step_mse(m, z) < before
    return m, y


def test_pure_python_forward_matches_numpy(trained_mlp: tuple[nn.MLP, np.ndarray]) -> None:
    m, _ = trained_mlp
    _, layers = core_mlp.parse(nn.export(m))
    m32 = [w.astype(np.float32).astype(np.float64) for w in m.weights()]
    xs = np.random.default_rng(1).normal(size=(50, m.w1.shape[1]))
    ref = nn.forward(m32, xs)
    got = np.array([core_mlp.forward(layers, list(map(float, row)))[0] for row in xs])
    assert np.max(np.abs(got - ref)) < 1e-5
    assert core_mlp.n_params(layers) == m.n_params


def test_edge_forecaster_matches_numpy_features(trained_mlp: tuple[nn.MLP, np.ndarray]) -> None:
    m, y = trained_mlp
    edge = core_mlp.MLPForecaster(nn.export(m))
    z = (y - m.mean) / m.std
    x, _ = nn.features(z, m.window, m.s1, m.s2)
    m32 = [w.astype(np.float32).astype(np.float64) for w in m.weights()]
    n = len(y)
    for k in range(n - 10):
        edge.update(float(y[k]), k * m.period)
    k = n - 11
    row = len(x) - (n - 1 - k)
    expected = (z[k] + nn.forward(m32, x[row : row + 1])[0]) * m.std + m.mean
    assert abs(edge.predict(1) - expected) < 1e-4 * m.std


@settings(max_examples=60, deadline=None)
@given(
    steps=st.lists(st.floats(-0.5, 0.5), min_size=20, max_size=150),
    eps=st.floats(0.01, 2.0),
    heartbeat=st.integers(1, 40),
)
def test_dual_prediction_bound_property(steps: list[float], eps: float, heartbeat: int) -> None:
    """Between transmissions the cloud value never differs from the edge value by more than eps."""
    period, s1, s2 = 300, 12, 24

    def model() -> HoltWinters:
        h = HoltWinters(period, s1, s2, 0.2, 0.1, 0.1, 0.7)
        for k in range(s2 * 3):
            h.update(10.0 + np.sin(k), k * period)
        return h

    tx, rx = DeltaSender(model(), eps, heartbeat * period, 0.01), DeltaReceiver(model())
    v = 10.0
    for i, dv in enumerate(steps):
        t = (s2 * 3 + i) * period
        v += dv
        sent, held = tx.offer(t, v)
        est = rx.step(t, held if sent else None)
        assert est == held
        assert abs(est - v) <= max(eps, 0.005) + 1e-9
