from __future__ import annotations

import numpy as np
import pandas as pd

from flowmine.central import chlorine, external, homogenise, maintenance, spatial, triage
from flowmine.edge.core import quality
from flowmine.testbed import network
from flowmine.testbed.build import Testbed


def _correlated(n: int, m: int, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    f = np.stack([np.sin(2 * np.pi * t / 288), np.cos(2 * np.pi * t / 96)], axis=1)
    w = rng.normal(size=(2, m))
    return 40 + 5 * f @ w + rng.normal(0, 0.05, size=(n, m))


def test_align_carries_slow_streams_forward_only_within_period() -> None:
    fr = pd.DataFrame(
        {
            "stream": ["L", "L", "P"],
            "time_s": [0, 1800, 300],
            "value": [1.0, 2.0, 5.0],
            "quality": [0, quality.make(quality.MISSING, 1), 0],
        }
    )
    g = homogenise.align(fr, ["L", "P"], 0, 2700, 300, {"L": 900, "P": 300})
    assert list(g.values[:3, 0]) == [1.0, 1.0, 1.0]
    assert np.isnan(g.values[3, 0]) and g.values[6, 0] == 2.0
    assert g.suspect[6, 0] and not g.suspect[0, 0]
    lab = pd.DataFrame({"result_s": [100, 5000]})
    assert len(homogenise.lab_available(lab, 1000)) == 1


def test_spatial_detects_bias_ignores_flagged_inputs_and_reconstructs() -> None:
    x = _correlated(6000, 8)
    m = spatial.SpatialModel([f"s{i}" for i in range(8)], 2, 1.0, 0.99).fit(x[:4000])
    cal = x[4000:5000]
    m.calibrate(cal, np.zeros_like(cal, dtype=bool), np.ones_like(cal, dtype=bool), 0.995)
    test = x[5000:].copy()
    test[200:300, 3] += 4.0
    r = m.apply(test, np.zeros_like(test, dtype=bool))
    f = m.flags(r)
    assert f[200:300, 3].mean() > 0.9 and f[:200].mean() < 0.02
    assert np.abs(r.estimate[200:300, 3] - x[5200:5300, 3]).mean() < 0.3
    sus = np.zeros_like(test, dtype=bool)
    sus[200:300, 3] = True
    r2 = m.apply(test, sus)
    others = np.delete(np.arange(8), 3)
    assert np.abs(r2.z[200:300][:, others]).max() < np.abs(r.z[200:300][:, others]).max()
    assert np.abs(r2.imputed[200:300, 3] - x[5200:5300, 3]).mean() < 0.3


def test_cusum_finds_drift_and_maintenance_resets_it() -> None:
    days = np.arange(40)
    d = np.random.default_rng(1).normal(0, 0.02, len(days))
    d[25:32] += np.linspace(0.02, 0.2, 7)
    diffs = pd.DataFrame(
        {
            "sensor_id": "C-1",
            "phenomenon_s": days * 86400,
            "result_s": days * 86400 + 30 * 3600,
            "d": d,
        }
    )
    base = chlorine.baseline(diffs[diffs.phenomenon_s < 20 * 86400], 0.02)
    alarms = chlorine.cusum(diffs[diffs.phenomenon_s >= 20 * 86400], base, 0.5, 5.0)
    up = [a for a in alarms if a.offset > 0]
    assert up and 25 * 86400 <= up[0].sample_s <= 31 * 86400
    assert up[0].alarm_s > up[0].sample_s and up[0].offset > 0.05
    log = pd.DataFrame(
        {"time_s": [26 * 86400 + 1], "asset": ["C-1"], "type": ["sensor_recalibration"]}
    )
    d2 = diffs.copy()
    d2.loc[d2.phenomenon_s > 26 * 86400, "d"] = 0.0
    assert not chlorine.cusum(d2[d2.phenomenon_s >= 20 * 86400], base, 0.5, 5.0, log)


def test_triage_separates_single_sensor_fault_from_coherent_event() -> None:
    n, m = 24, 6
    temporal = np.zeros((n, m), dtype=bool)
    spatial_f = np.zeros((n, m), dtype=bool)
    temporal[0:6, 2] = True
    spatial_f[0:6, 2] = True
    temporal[12:18, [0, 1, 3, 4]] = True
    r = triage.triage(temporal, spatial_f, 6)
    assert r.fault[0:6, 2].all() and not r.event[0:6].any()
    assert r.event[12:18].all() and not r.fault[12:18].any()
    assert r.event_sensors[12, [0, 1, 3, 4]].all() and not r.event_sensors[0, 2]


def test_temperature_covariate_helps_when_demand_depends_on_it() -> None:
    h = np.arange(24 * 7 * 8)
    rng = np.random.default_rng(0)
    temp = 20 + 5 * np.sin(2 * np.pi * h / 24) + np.cumsum(rng.normal(0, 0.3, len(h)))
    dem = 500 + 50 * np.sin(2 * np.pi * h / 24) + 8 * (temp - 20) + rng.normal(0, 2, len(h))
    s = external.score(
        pd.Series(dem, index=h), pd.Series(temp, index=h), 24 * 7 * 6, (24 * 7 * 7, 24 * 7 * 8)
    )
    assert s.mae_with < 0.7 * s.mae_without


def test_affected_sensors_are_near_the_asset(smoke_tb: Testbed) -> None:
    wn = network.load_network(None)
    g = network.graph(wn)
    near = maintenance.affected_sensors(wn, g, "10", smoke_tb.sensors)
    assert "F-10" in near and len(near) < len(smoke_tb.sensors)
    times = np.arange(0, 7200, 300)
    log = pd.DataFrame({"time_s": [600], "asset": ["10"], "type": ["pump_maintenance"]})
    mask = maintenance.suppression_mask(times, ["F-10", "P-15"], log, {"10": ["F-10"]}, 1.0)
    assert mask[2:14, 0].all() and not mask[:2, 0].any() and not mask[:, 1].any()
