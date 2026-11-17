"""Ground-truth hydraulics and chlorine with EPANET (through WNTR's EpanetSimulator).

Process events are real hydraulic changes, not sensor faults:

* pipe leak: the pipe is split and the leak is a demand at the new junction,
  ``Q = Cd * A * sqrt(2 g p)`` (the leak equation of WNTR's leak model) with ``p`` the
  pressure of a first, leak-free run, ramped in over ``leak_ramp_h``. WNTR's own
  ``add_leak`` works only with its WNTRSimulator, which has no water-quality solver,
  so the leak equation is applied here as an EPANET demand.
* demand surge: extra demand at one junction for a few hours (hydrant use, a burst
  being filled, an industrial draw).
* operations, which the maintenance log records: closing a pipe (valve operation) and
  taking the Lake pump out of service for a few hours (pump maintenance).
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd
import wntr
from wntr.network.controls import Control, ControlAction, ControlPriority, SimTimeCondition

from flowmine.config import Config
from flowmine.testbed import demand, network

log = logging.getLogger(__name__)
G = 9.81
DAY = 86400


@dataclass(frozen=True)
class ProcessEvent:
    event_id: int
    event_type: str  # leak | surge | valve_operation | pump_maintenance
    element: str  # pipe, junction or pump id
    start_s: int
    end_s: int
    size: float  # leak area m2, surge L/s, 0 for operations
    period: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def assign_demands(wn: wntr.network.WaterNetworkModel, mult: np.ndarray) -> dict[str, float]:
    """Replace each junction's demands with one entry: mean base demand x own multipliers.

    Returns the base demand per junction (m3/s), which is what a model of the network
    without measurements would use.
    """
    default_pat = wn.options.hydraulic.pattern
    names = sorted(wn.junction_name_list)
    base: dict[str, float] = {}
    for j, name in enumerate(names):
        junc = wn.get_node(name)
        total = 0.0
        for d in junc.demand_timeseries_list:
            pat = d.pattern
            if pat is None and default_pat is not None and default_pat in wn.pattern_name_list:
                pat = wn.get_pattern(default_pat)
            total += d.base_value * (float(np.mean(pat.multipliers)) if pat is not None else 1.0)
        base[name] = total
        junc.demand_timeseries_list.clear()
        if total > 0:
            wn.add_pattern(f"m_{name}", list(mult[:, j]))
            junc.add_demand(total, f"m_{name}")
    return base


def _window(n: int, step: int, start: int, end: int, edge_s: int = 900) -> np.ndarray:
    """0/1 pattern for [start, end) with linear edges of ``edge_s`` seconds."""
    t = np.arange(n) * step
    up = np.clip((t - start) / edge_s, 0.0, 1.0)
    down = np.clip((end - t) / edge_s, 0.0, 1.0)
    return np.minimum(up, down)


def _bridges(g: nx.Graph) -> set[frozenset[str]]:
    return {frozenset(e) for e in nx.bridges(g)}


def plan_events(
    wn: wntr.network.WaterNetworkModel,
    rng: np.random.Generator,
    cfg: Config,
    periods: dict[str, tuple[int, int]],
    sensor_nodes: list[str],
) -> list[ProcessEvent]:
    """Choose times and places of the process events (non-overlapping in time)."""
    ev = cfg.events
    g = network.graph(wn)
    bridges = _bridges(g)
    pipes = [
        p
        for p in sorted(wn.pipe_name_list)
        if wn.get_link(p).start_node_name in wn.junction_name_list
        and wn.get_link(p).end_node_name in wn.junction_name_list
        and wn.get_link(p).length > 100
    ]
    demand_nodes = [
        j
        for j in sorted(wn.junction_name_list)
        if wn.get_node(j).demand_timeseries_list and j not in sensor_nodes
    ]
    near_sensor_pipes = [
        p
        for p in pipes
        if frozenset((wn.get_link(p).start_node_name, wn.get_link(p).end_node_name)) not in bridges
        and (
            wn.get_link(p).start_node_name in sensor_nodes
            or wn.get_link(p).end_node_name in sensor_nodes
        )
    ]
    lake_pump = sorted(wn.pump_name_list)[0]
    out: list[ProcessEvent] = []
    eid = 0
    for pname, (t0, t1) in periods.items():
        busy: list[tuple[int, int]] = []
        n_leak = ev.leaks_calib if pname == "calib" else ev.leaks_test
        for _ in range(n_leak):
            start = int(rng.uniform(t0 + 0.45 * (t1 - t0), t0 + 0.6 * (t1 - t0))) // 300 * 300
            out.append(
                ProcessEvent(eid, "leak", str(rng.choice(pipes)), start, t1, ev.leak_area_m2, pname)
            )
            busy.append((start, t1))
            eid += 1
        jobs = (
            ["surge"] * (ev.surges_calib if pname == "calib" else ev.surges_test)
            + ["valve_operation", "pump_maintenance"]
            * ((ev.operations_calib if pname == "calib" else ev.operations_test) // 2)
            + (
                ["valve_operation"]
                if (ev.operations_calib if pname == "calib" else ev.operations_test) % 2
                else []
            )
        )
        for kind in jobs:
            for _attempt in range(500):
                lo, hi = ev.surge_hours if kind == "surge" else ev.operation_hours
                dur = int(rng.uniform(lo, hi) * 3600) // 300 * 300
                if kind == "pump_maintenance":
                    day = int(rng.integers(t0 // DAY, t1 // DAY))
                    start = day * DAY + int(rng.uniform(4, 10) * 3600) // 300 * 300
                else:
                    start = int(rng.uniform(t0 + 3600, t1 - dur - 3600)) // 300 * 300
                end = start + dur
                if end > t1 or any(start < b + 8 * 3600 and a < end + 8 * 3600 for a, b in busy):
                    continue
                if kind == "surge":
                    el, size = str(rng.choice(demand_nodes)), float(rng.uniform(*ev.surge_l_s))
                elif kind == "valve_operation":
                    el, size = str(rng.choice(near_sensor_pipes)), 0.0
                else:
                    el, size = lake_pump, 0.0
                out.append(ProcessEvent(eid, kind, el, start, end, size, pname))
                busy.append((start, end))
                eid += 1
                break
    return out


def apply_events(
    wn: wntr.network.WaterNetworkModel,
    events: list[ProcessEvent],
    base_pressure: pd.DataFrame,
    cfg: Config,
    n_steps: int,
) -> None:
    step = cfg.network.hydraulic_step_s
    ev = cfg.events
    for e in events:
        if e.event_type == "leak":
            pipe = wn.get_link(e.element)
            p = base_pressure[pipe.start_node_name].to_numpy()[:n_steps]
            q = ev.leak_discharge_coeff * e.size * np.sqrt(2 * G * np.clip(p, 0.0, None))
            ramp = np.clip((np.arange(n_steps) * step - e.start_s) / (ev.leak_ramp_h * 3600), 0, 1)
            node = f"LEAK-{e.element}"
            wntr.morph.split_pipe(wn, e.element, f"{e.element}_B", node, return_copy=False)
            wn.add_pattern(f"leak_{e.event_id}", list(q * ramp))
            wn.get_node(node).add_demand(1.0, f"leak_{e.event_id}")
        elif e.event_type == "surge":
            pat = _window(n_steps, step, e.start_s, e.end_s)
            wn.add_pattern(f"surge_{e.event_id}", list(pat))
            wn.get_node(e.element).add_demand(e.size / 1000.0, f"surge_{e.event_id}")
        else:
            link = wn.get_link(e.element)
            closed, opened = wntr.network.LinkStatus.Closed, wntr.network.LinkStatus.Open
            for name, t, status in (
                (f"op{e.event_id}_a", e.start_s, closed),
                (f"op{e.event_id}_b", e.end_s, opened),
            ):
                ctl = Control(
                    SimTimeCondition(wn, "=", float(t)),
                    ControlAction(link, "status", status),
                    priority=ControlPriority.high,
                )
                wn.add_control(name, ctl)


def build_network(
    cfg: Config, rng: np.random.Generator, n_days: int
) -> tuple[wntr.network.WaterNetworkModel, dict[str, float], np.ndarray, np.ndarray]:
    """Network with generated demands; returns (wn, base demands, temperature, mult)."""
    wn = network.load_network(cfg.network.inp)
    network.convert_time_controls(wn)
    duration = n_days * DAY
    network.configure(wn, cfg, duration)
    step = cfg.network.hydraulic_step_s
    n_steps = duration // step + 1
    temp = demand.air_temperature(rng, n_days * 24 + 1, cfg.weather)
    xy_all = network.coordinates(wn)
    names = sorted(wn.junction_name_list)
    xy = np.array([xy_all[n] for n in names])
    length = cfg.demand.spatial_length_frac * network.extent(wn)
    mult = demand.multipliers(rng, np.arange(n_steps) * step, temp, xy, length, cfg.demand)
    base = assign_demands(wn, mult)
    return wn, base, temp, mult


def run(wn: wntr.network.WaterNetworkModel) -> wntr.sim.SimulationResults:
    """Run EPANET in a private temporary directory (parallel runs must not share files)."""
    with tempfile.TemporaryDirectory() as tmp:
        return wntr.sim.EpanetSimulator(wn).run_sim(file_prefix=str(Path(tmp) / "run"))
