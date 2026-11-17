from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from flowmine.config import Config, EventConfig
from flowmine.sta.provision import thing_body
from flowmine.testbed import build, network
from flowmine.testbed.build import Testbed
from flowmine.twin.bridge import Bridge, BridgeSensor, StepInputs, sensors_from_things
from flowmine.twin.runner import boundary_from, inputs_from_frame, run_twin, step_inputs


def _bridge_sensors(tb: Testbed) -> list[BridgeSensor]:
    things = [
        thing_body(st, [s for s in tb.sensors if s.station == st.station_id], "DMA-x")
        for st in tb.stations
    ]
    return sensors_from_things(things)


@pytest.fixture(scope="module")
def quiet_tb(smoke_cfg: Config) -> Testbed:
    """Smoke testbed without process events: the reference is the plain simulation."""
    cfg = dataclasses.replace(
        smoke_cfg,
        events=EventConfig(
            leaks_test=0, surges_calib=0, surges_test=0, operations_calib=0, operations_test=0
        ),
    )
    return build.generate(cfg, 1)


def test_mapping_comes_from_thing_properties(smoke_tb: Testbed) -> None:
    bs = _bridge_sensors(smoke_tb)
    assert {b.sensor_id for b in bs} == {s.sensor_id for s in smoke_tb.sensors}
    tank = next(b for b in bs if b.kind == "level")
    assert tank.tank_area_m2 and tank.tank_area_m2 > 0


def test_bridge_reproduces_reference_with_true_inputs(quiet_tb: Testbed) -> None:
    """Noise-free levels (only when observed), true pump flows and true demands."""
    cfg = quiet_tb.cfg
    t0 = quiet_tb.periods["test"][0]
    n = 288
    pres = [s.element for s in quiet_tb.sensors if s.kind == "pressure"]
    truth = quiet_tb.wide("truth")
    br = Bridge(None, 300, n * 300 + 300, ["10"], pres, ["60"], cfg)
    worst = 0.0
    try:
        for k in range(n):
            t = t0 + k * 300
            levels = {tk: float(truth.at[t, f"L-{tk}"]) for tk in "123" if t % 900 == 0}
            out = br.step(
                StepInputs(
                    levels,
                    {"10": float(truth.at[t, "F-10"])},
                    node_demands=quiet_tb.demand.loc[t].to_dict(),
                )
            )
            worst = max(worst, max(abs(out.pressure[p] - quiet_tb.nodes.at[t, p]) for p in pres))
    finally:
        br.close()
    assert worst < 0.01


def test_water_balance_tracks_true_total_demand(quiet_tb: Testbed) -> None:
    wn = network.load_network(None)
    b = boundary_from(wn, _bridge_sensors(quiet_tb))
    assert set(b.sources) == {"F-10", "F-60"} and set(b.levels) == {"1", "2", "3"}
    t0 = quiet_tb.periods["test"][0]
    times = np.arange(t0, t0 + 86400, 300)
    frame = quiet_tb.truth.rename(columns={"sensor_id": "stream"}).assign(quality=0)
    streams = sorted(
        set(b.sources)
        | {v[0] for v in b.inlets.values()}
        | set(b.levels.values())
        | set(b.pumps.values())
    )
    est = np.array(
        [si.demand_l_s for si in step_inputs(inputs_from_frame(frame, streams, times), b, 300)]
    )
    true = quiet_tb.demand.reindex(times).sum(axis=1).to_numpy()
    assert np.max(np.abs(est - true) / true) < 1e-3


def test_missing_boundary_input_marks_step_degraded(quiet_tb: Testbed) -> None:
    wn = network.load_network(None)
    b = boundary_from(wn, _bridge_sensors(quiet_tb))
    t0 = quiet_tb.periods["test"][0]
    times = np.arange(t0, t0 + 3600, 300)
    frame = quiet_tb.truth.rename(columns={"sensor_id": "stream"}).assign(quality=0)
    frame = frame[~((frame.stream == "F-60") & (frame.time_s == t0 + 1200))]
    streams = sorted(
        set(b.sources)
        | {v[0] for v in b.inlets.values()}
        | set(b.levels.values())
        | set(b.pumps.values())
    )
    steps = list(step_inputs(inputs_from_frame(frame, streams, times), b, 300))
    assert steps[4].degraded and not steps[3].degraded
    assert steps[4].demand_l_s == steps[3].demand_l_s


def test_leak_shows_as_growing_residual(smoke_tb: Testbed) -> None:
    cfg = smoke_tb.cfg
    leak = smoke_tb.events[smoke_tb.events.event_type == "leak"].iloc[0]
    t0, t1 = int(leak.start_s) - 86400, smoke_tb.periods["test"][1]
    frame = smoke_tb.truth.rename(columns={"sensor_id": "stream"}).assign(quality=0)
    r = run_twin(
        cfg,
        network.load_network(None),
        _bridge_sensors(smoke_tb),
        frame,
        t0,
        t1,
        ["123"],
        smoke_tb.wide("truth"),
    )
    res = r.residual.filter(like="RES-P")
    before = res[res.index < leak.start_s].mean().abs().mean()
    after = res[res.index >= leak.start_s + 6 * 3600].mean().abs().mean()
    assert after > before + 1.0
    assert "VIRT-123" in r.simulated and np.isfinite(r.simulated["VIRT-123"]).all()
