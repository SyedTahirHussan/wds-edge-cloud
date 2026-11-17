"""Drive the bridge from aligned observations and collect simulated streams.

Boundary rules per period t:

* a tank level is applied only when a new level observation exists at t; a flagged
  one is replaced by the edge reconstruction if present, otherwise skipped (the model
  integrates the tank itself) and the step is marked degraded;
* a measured pump's status comes from its flow; flagged without reconstruction ->
  hold the last value, degraded;
* the total demand is the sum of source-meter flows minus the tank inflows (inlet
  meters times their sign); when an inlet meter is unusable the tank inflow is taken
  from the level change times the area; when a source meter is unusable the last
  total demand is held and the step is degraded.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np
import pandas as pd
import wntr

from flowmine.config import Config
from flowmine.edge.core import quality
from flowmine.twin.bridge import Bridge, BridgeSensor, StepInputs, StepOutputs


@dataclass
class Boundary:
    levels: dict[str, str]  # tank -> level stream
    pumps: dict[str, str]  # measured pump -> flow stream
    sources: list[str]  # source meter streams (flow into the network)
    inlets: dict[str, tuple[str, float, float]]  # tank -> (inlet stream, sign, area m2)


def boundary_from(wn: wntr.network.WaterNetworkModel, sensors: list[BridgeSensor]) -> Boundary:
    res = set(wn.reservoir_name_list)
    levels, pumps, sources, inlets = {}, {}, [], {}
    for s in sensors:
        if s.kind == "level":
            levels[s.element] = s.sensor_id
        if s.kind != "flow":
            continue
        link = wn.get_link(s.element)
        if s.element_type == "pump":
            pumps[s.element] = s.sensor_id
        if link.start_node_name in res or link.end_node_name in res:
            sources.append(s.sensor_id)
        for tank in (link.start_node_name, link.end_node_name):
            if tank in wn.tank_name_list:
                inlets[tank] = (s.sensor_id, s.inflow_sign, float(s.tank_area_m2 or 0.0))
    return Boundary(levels, pumps, sources, inlets)


@dataclass
class Inputs:
    """Aligned boundary observations as arrays per stream (row i = times[i])."""

    times: np.ndarray
    values: dict[str, np.ndarray]
    usable: dict[str, np.ndarray]  # observed and either unflagged or reconstructed
    fresh: dict[str, np.ndarray]  # an observation (not a carried value) exists at t

    def row(self, t: int) -> int:
        return int(np.searchsorted(self.times, t))


def inputs_from_frame(frame: pd.DataFrame, streams: list[str], times: np.ndarray) -> Inputs:
    """From a tidy observation frame; values flagged without reconstruction are unusable."""
    sub = frame[frame.stream.isin(streams)]
    v = sub.pivot_table(index="time_s", columns="stream", values="value", aggfunc="last")
    q = sub.pivot_table(index="time_s", columns="stream", values="quality", aggfunc="last")
    v = v.reindex(index=times, columns=streams)
    q = q.reindex(index=times, columns=streams).fillna(0).astype(np.int64)
    values, usable, fresh = {}, {}, {}
    for s in streams:
        vs = v[s].to_numpy(dtype=float)
        qs = q[s].to_numpy(dtype=np.int64)
        bad = (qs & (quality.MISSING | quality.REJECTING | quality.CROSSCHECK)) != 0
        rec = (qs & quality.RECONSTRUCTED) != 0
        values[s] = vs
        fresh[s] = np.isfinite(vs)
        usable[s] = fresh[s] & (~bad | rec)
    return Inputs(np.asarray(times), values, usable, fresh)


class StepBuilder:
    """Applies the boundary rules one period at a time (keeps the held values)."""

    def __init__(self, b: Boundary) -> None:
        self.b = b
        self.last_pump: dict[str, float] = {}
        self.last_level: dict[str, tuple[int, float]] = {}
        self.last_inflow: dict[str, float] = {}
        self.last_demand: float | None = None

    def at(self, inp: Inputs, t: int) -> StepInputs:
        b = self.b
        i = inp.row(t)
        degraded = False
        levels = {}
        for tank, s in b.levels.items():
            if not inp.fresh[s][i]:
                continue
            if not inp.usable[s][i]:
                degraded = True
                continue
            v = float(inp.values[s][i])
            levels[tank] = v
            prev = self.last_level.get(tank)
            if prev is not None and t > prev[0]:
                area = b.inlets.get(tank, ("", 1.0, 0.0))[2]
                self.last_inflow[tank] = 1000.0 * (v - prev[1]) * area / (t - prev[0])
            self.last_level[tank] = (t, v)
        pumps = {}
        for pump, s in b.pumps.items():
            if inp.usable[s][i]:
                self.last_pump[pump] = float(inp.values[s][i])
            elif pump in self.last_pump:
                degraded = True
            pumps[pump] = self.last_pump.get(pump, 0.0)
        if all(inp.usable[s][i] for s in b.sources):
            total = float(sum(inp.values[s][i] for s in b.sources))
            for tank, (s, sign, _area) in b.inlets.items():
                if inp.usable[s][i]:
                    total -= sign * float(inp.values[s][i])
                else:
                    total -= self.last_inflow.get(tank, 0.0)
                    degraded = True
            self.last_demand = total
        else:
            degraded = True
        return StepInputs(levels, pumps, self.last_demand, None, degraded)


def step_inputs(inp: Inputs, b: Boundary, step_s: int) -> Iterator[StepInputs]:
    builder = StepBuilder(b)
    for t in inp.times:
        yield builder.at(inp, int(t))


def boundary_streams(b: Boundary) -> list[str]:
    return sorted(
        set(b.levels.values())
        | set(b.pumps.values())
        | set(b.sources)
        | {v[0] for v in b.inlets.values()}
    )


def outputs_row(
    out: StepOutputs, sensors: list[BridgeSensor], virtual_nodes: list[str]
) -> dict[str, float]:
    row: dict[str, float] = {}
    for s in sensors:
        if s.kind == "pressure":
            row[f"SIM-{s.sensor_id}"] = out.pressure[s.element]
        elif s.kind == "chlorine":
            row[f"SIM-{s.sensor_id}"] = out.chlorine[s.element]
        elif s.kind == "flow":
            row[f"SIM-{s.sensor_id}"] = out.flow[s.element]
    for n in virtual_nodes:
        row[f"VIRT-{n}"] = out.pressure[n]
    return row


def report_elements(
    sensors: list[BridgeSensor], virtual_nodes: list[str]
) -> tuple[list[str], list[str]]:
    nodes = sorted(
        {s.element for s in sensors if s.kind in ("pressure", "chlorine")} | set(virtual_nodes)
    )
    links = sorted({s.element for s in sensors if s.kind == "flow"})
    return nodes, links


@dataclass
class TwinRun:
    simulated: pd.DataFrame  # time_s x stream (SIM-<sensor>, VIRT-<node>)
    residual: pd.DataFrame  # time_s x RES-<sensor> (measured - simulated)
    degraded: np.ndarray
    resyncs: int


def run_twin(
    cfg: Config,
    wn: wntr.network.WaterNetworkModel,
    sensors: list[BridgeSensor],
    frame: pd.DataFrame,
    t0: int,
    t1: int,
    virtual_nodes: list[str],
    measured: pd.DataFrame | None = None,
) -> TwinRun:
    """Run the bridge over [t0, t1) driven by ``frame`` (tidy observations)."""
    step = cfg.network.hydraulic_step_s
    times = np.arange(t0, t1, step)
    b = boundary_from(wn, sensors)
    inp = inputs_from_frame(frame, boundary_streams(b), times)
    nodes, links = report_elements(sensors, virtual_nodes)
    bridge = Bridge(cfg.network.inp, step, t1 - t0 + step, sorted(b.pumps), nodes, links, cfg)
    rows, degraded = [], []
    try:
        for si in step_inputs(inp, b, step):
            out = bridge.step(si)
            rows.append(outputs_row(out, sensors, virtual_nodes))
            degraded.append(out.degraded)
        resyncs = bridge.resyncs
    finally:
        bridge.close()
    sim = pd.DataFrame(rows, index=times)
    sim.index.name = "time_s"
    res = pd.DataFrame(index=times)
    if measured is not None:
        for s in sensors:
            col = f"SIM-{s.sensor_id}"
            if col in sim and s.sensor_id in measured:
                res[f"RES-{s.sensor_id}"] = measured[s.sensor_id].reindex(times) - sim[col]
    return TwinRun(sim, res, np.array(degraded), resyncs)
