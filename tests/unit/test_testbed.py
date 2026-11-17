from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from flowmine.config import Config
from flowmine.testbed import build, faults, simulate
from flowmine.testbed.faults import FaultEvent


def test_generation_is_deterministic(smoke_cfg: Config, smoke_tb: build.Testbed) -> None:
    again = build.generate(smoke_cfg, 1)
    pd.testing.assert_frame_equal(again.observed, smoke_tb.observed)
    pd.testing.assert_frame_equal(again.labels, smoke_tb.labels)
    pd.testing.assert_frame_equal(again.lab, smoke_tb.lab)
    other = build.generate(smoke_cfg, 2)
    assert not np.allclose(other.truth.value.to_numpy(), smoke_tb.truth.value.to_numpy())


def test_saved_testbed_roundtrips(smoke_cfg: Config, smoke_tb: build.Testbed, tmp_path) -> None:
    build.save(smoke_tb, tmp_path)
    back = build.load(smoke_cfg, tmp_path)
    pd.testing.assert_frame_equal(back.observed, smoke_tb.observed)
    assert back.stations == smoke_tb.stations and back.sensors == smoke_tb.sensors


def test_water_balance_closes(smoke_cfg: Config) -> None:
    """Source inflow minus tank inflow equals the sum of junction demands at every step."""
    rng = build.rng_for(1, "demand")
    wn, _, _, _ = simulate.build_network(smoke_cfg, rng, 3)
    res = simulate.run(wn)
    q = res.link["flowrate"]
    inflow = q["60"] + q["10"]
    into_tanks = 0.0
    for tank in wn.tank_name_list:
        for link in wn.get_links_for_node(tank):
            sign = -1.0 if wn.get_link(link).start_node_name == tank else 1.0
            into_tanks = into_tanks + sign * q[link]
    demand = res.node["demand"][wn.junction_name_list].sum(axis=1)
    err = (inflow - into_tanks - demand).abs()
    assert float(err.max()) < 1e-3 * float(demand.mean())


def test_sampling_periods_respected(smoke_tb: build.Testbed) -> None:
    for s in smoke_tb.sensors:
        t = smoke_tb.observed.loc[smoke_tb.observed.sensor_id == s.sensor_id, "time_s"].to_numpy()
        assert set(np.diff(t)) == {s.period_s}
        assert t[0] == 0 and t[-1] == smoke_tb.cfg.total_days * 86400 - s.period_s


def test_observed_is_quantised_noisy_truth_outside_faults(smoke_tb: build.Testbed) -> None:
    clean = smoke_tb.labels.fault_id.to_numpy() < 0
    obs = smoke_tb.observed.value.to_numpy()[clean]
    tru = smoke_tb.truth.value.to_numpy()[clean]
    assert np.all(np.isfinite(obs))
    for s in smoke_tb.sensors:
        m = (smoke_tb.observed.sensor_id == s.sensor_id).to_numpy()[clean]
        steps = obs[m] / s.resolution
        assert np.allclose(steps, np.round(steps), atol=1e-6)
        sd = smoke_tb.cfg.sensors[s.kind].noise_sd
        assert (
            np.abs(obs[m] - tru[m]).max() < 6 * sd + 5 * s.resolution + 0.05 * np.abs(tru[m]).max()
        )


def test_faults_only_in_labelled_periods_and_all_types_present(smoke_tb: build.Testbed) -> None:
    f = smoke_tb.faults
    assert set(f.fault_type) == set(faults.FAULT_TYPES)
    train_end = smoke_tb.periods["train"][1]
    lab = smoke_tb.labels
    assert (lab.loc[lab.fault_id >= 0, "time_s"] >= train_end).all()
    gaps = lab.fault_type.isin(faults.GAPS).to_numpy()
    assert np.isnan(smoke_tb.observed.value.to_numpy()[gaps]).all()


@pytest.mark.parametrize("ftype", faults.FAULT_TYPES)
def test_each_injector_changes_exactly_the_labelled_samples(ftype: str) -> None:
    rng = np.random.default_rng(0)
    v = np.linspace(10.0, 20.0, 200)
    before = v.copy()
    n = 1 if ftype == "spike" else 17
    ev = FaultEvent(0, "X", ftype, 50, n, 2.5 if ftype != "noise_burst" else 0.5, "test")
    faults.inject(v, ev, rng)
    changed = ~((v == before) | (np.isnan(v) & np.isnan(before)))
    expected = np.zeros(200, dtype=bool)
    expected[50 : 50 + n] = True
    assert np.array_equal(changed, expected)
    if ftype == "stuck":
        assert np.all(v[50 : 50 + n] == before[49])
    if ftype == "drift":
        assert np.isclose(v[50 + n - 1] - before[50 + n - 1], 2.5)


def test_process_events_are_labelled_and_leak_is_simulated(smoke_tb: build.Testbed) -> None:
    ev = smoke_tb.events
    assert {"surge", "valve_operation", "leak"} <= set(ev.event_type)
    lab = smoke_tb.labels
    leak = int(ev.loc[ev.event_type == "leak", "event_id"].iloc[0])
    assert (lab.event_id == leak).sum() > 50
    leak_cols = [c for c in smoke_tb.demand.columns if c.startswith("LEAK-")]
    assert len(leak_cols) == 1 and smoke_tb.demand[leak_cols[0]].max() > 10.0


def test_lab_and_maintenance_sources(smoke_tb: build.Testbed) -> None:
    lab = smoke_tb.lab
    delay_h = (lab.result_s - lab.phenomenon_s) / 3600
    assert delay_h.between(24, 48).all()
    assert ((lab.phenomenon_s // 86400) % 7 < 5).all()
    n_cl = sum(s.kind == "chlorine" for s in smoke_tb.sensors)
    assert len(lab) == n_cl * sum(d % 7 < 5 for d in range(smoke_tb.cfg.total_days))
    m = smoke_tb.maintenance
    drifts = smoke_tb.faults[smoke_tb.faults.fault_type == "drift"]
    recal = m[m.type == "sensor_recalibration"]
    for _, d in drifts.iterrows():
        per = smoke_tb.sensor(d.sensor_id).period_s
        assert ((recal.asset == d.sensor_id) & (recal.time_s == (d.start_idx + d.n) * per)).any()
    assert set(m.type) >= {"sensor_recalibration", "valve_operation"}
    assert smoke_tb.weather.time_s.diff().dropna().eq(3600).all()
