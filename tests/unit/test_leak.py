"""Leak localisation: sensitivity matrix, ranking, the E9 case generator, the watcher."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import numpy as np
import pytest

from flowmine.central import leak
from flowmine.config import Config
from flowmine.experiments import leak_loc
from flowmine.sta import mapping
from flowmine.testbed.build import Testbed

START = datetime.fromisoformat("2025-06-02T00:00:00+00:00")
MONITORED = ["10", "101", "15", "219", "253"]


@pytest.fixture(scope="module")
def sens(smoke_cfg: Config) -> leak.Sensitivity:
    return leak.compute(smoke_cfg, MONITORED, np.ones(leak.STEPS_PER_DAY))


def test_leak_drops_pressure_most_at_its_own_node(sens: leak.Sensitivity) -> None:
    assert sens.s.shape == (24, len(MONITORED), 92)
    for i, node in enumerate(MONITORED[1:], start=1):
        col = sens.s[:, :, sens.candidates.index(node)].mean(axis=0)
        assert col[i] < 0
        assert int(np.argmin(col)) == i


def test_ranking_recovers_a_noise_free_signature(sens: leak.Sensitivity) -> None:
    hours = np.arange(6, 12)
    for node in ("123", "197", "253"):
        r = 20.0 * sens.at_hours(hours)[:, sens.candidates.index(node)]
        for method in ("correlation", "angle"):
            order, sc = leak.ranking(sens, r, hours, method)
            assert order[0] == node and sc[0] == pytest.approx(1.0)
        r[0] = np.nan  # a missing sensor is left out
        order, _ = leak.ranking(sens, r, hours)
        assert leak.rank_of(order, [node]) <= 3


def test_cache_roundtrip(smoke_cfg: Config, sens: leak.Sensitivity, tmp_path) -> None:
    mult = np.ones(leak.STEPS_PER_DAY)
    a = leak.ensure(smoke_cfg, MONITORED, mult, tmp_path)
    b = leak.ensure(smoke_cfg, MONITORED, mult, tmp_path)
    assert len(list(tmp_path.glob("sensitivity-*.npz"))) == 1
    assert a.candidates == b.candidates and np.array_equal(a.s, b.s)
    assert np.allclose(a.s, sens.s)


def test_case_generator_base_run_matches_the_testbed(smoke_cfg: Config, smoke_tb: Testbed) -> None:
    ctx = SimpleNamespace(cfg=smoke_cfg, seed=1, tb=smoke_tb)
    t0 = smoke_tb.periods["test"][0]
    wn, _ = leak_loc.truth_network(ctx, t0 + 6 * 3600)  # type: ignore[arg-type]
    from flowmine.testbed import simulate

    v = leak_loc.sensor_values(simulate.run(wn), smoke_tb.sensors)
    truth = smoke_tb.wide("truth")
    for sid in ("P-10", "F-10", "L-1"):
        col = truth[sid].dropna()
        t = col.index[(col.index >= t0) & (col.index < t0 + 6 * 3600)]
        assert len(t) >= 24 and np.allclose(
            v.loc[t, sid].to_numpy(), col.loc[t].to_numpy(), atol=1e-3
        )


def test_leak_candidates_observation_body() -> None:
    body = mapping.leak_candidates_body(
        START,
        7200,
        ["a", "b", "c", "d", "e", "f"],
        [0.9, 0.8, 0.7, 0.6, 0.5, 0.4],
        3600,
        "angle",
        3,
    )
    assert body["Datastream"] == {"@iot.id": 3}
    assert [r["node"] for r in body["result"]] == ["a", "b", "c", "d", "e"]
    assert body["result"][0] == {"rank": 1, "node": "a", "score": 0.9}
    assert body["parameters"]["window_start"] == mapping.iso(START, 3600)
    assert body["phenomenonTime"] == mapping.iso(START, 7200)


def test_watcher_publishes_when_the_residual_is_large(sens: leak.Sensitivity) -> None:
    sensor_node = {f"P-{n}": n for n in MONITORED}
    w = leak.LeakWatcher(sens, sensor_node, window_s=3600, every_s=3600, alarm_norm_m=0.3)
    col = sens.s[:, :, sens.candidates.index("197")]
    out = None
    for t in range(0, 7200 + 300, 300):
        h = (t % 86400) // 3600
        res = {f"P-{n}": 30.0 * col[h, i] for i, n in enumerate(MONITORED)}
        hit = w.add(t, res)
        out = hit or out
    assert out is not None and out[0][0] == "197"
    quiet = leak.LeakWatcher(sens, sensor_node, window_s=3600, every_s=3600, alarm_norm_m=0.3)
    assert all(quiet.add(t, {k: 0.01 for k in sensor_node}) is None for t in range(0, 7500, 300))
