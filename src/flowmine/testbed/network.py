"""Load an EPANET model and prepare it for a multi-week extended-period simulation."""

from __future__ import annotations

import logging
from pathlib import Path

import networkx as nx
import numpy as np
import wntr
from wntr.network.controls import Control, ControlAction, SimTimeCondition, TimeOfDayCondition

from flowmine.config import Config

log = logging.getLogger(__name__)

DAY = 86400


def default_inp() -> Path:
    """Net3 as shipped inside the installed WNTR package (``wntr.library``)."""
    return Path(wntr.library.model_library.get_filepath("Net3"))


def load_network(path: str | Path | None) -> wntr.network.WaterNetworkModel:
    return wntr.network.WaterNetworkModel(str(path or default_inp()))


def convert_time_controls(wn: wntr.network.WaterNetworkModel) -> int:
    """Turn ``AT TIME`` controls into daily ``AT CLOCKTIME`` controls.

    Net3 schedules its Lake pump with absolute times covering only the first week;
    a multi-week run needs the same schedule every day. Returns the number of
    clock-time controls created.
    """
    seen: set[tuple[str, str, int, int]] = set()
    remove = []
    for name, ctl in wn.controls():
        cond = ctl.condition
        if not isinstance(cond, SimTimeCondition):
            continue
        remove.append(name)
        for act in ctl.actions():
            target, attr = act.target()
            key = (target.name, attr, int(cond._threshold) % DAY, int(act._value))
            if key in seen:
                continue
            seen.add(key)
    for name in remove:
        wn.remove_control(name)
    for i, (link_name, attr, tod, value) in enumerate(sorted(seen)):
        link = wn.get_link(link_name)
        cond = TimeOfDayCondition(wn, "=", float(tod), repeat=True)
        wn.add_control(f"daily_{i}", Control(cond, ControlAction(link, attr, value)))
    log.info("converted %d time controls into %d daily controls", len(remove), len(seen))
    return len(seen)


def configure(wn: wntr.network.WaterNetworkModel, cfg: Config, duration_s: int) -> None:
    """Set times and the chlorine model (first-order bulk and wall decay)."""
    step = cfg.network.hydraulic_step_s
    t = wn.options.time
    t.duration = duration_s
    t.hydraulic_timestep = step
    t.pattern_timestep = step
    t.report_timestep = step
    t.quality_timestep = step
    t.rule_timestep = step
    t.start_clocktime = 0
    t.pattern_start = 0
    q = wn.options.quality
    q.parameter = "CHEMICAL"
    q.chemical_name = "Chlorine"
    q.inpfile_units = "mg/L"
    r = wn.options.reaction
    r.bulk_order = 1.0
    r.wall_order = 1.0
    r.bulk_coeff = cfg.quality.bulk_coeff_per_day / DAY
    r.wall_coeff = cfg.quality.wall_coeff_m_per_day / DAY
    for _, res in wn.reservoirs():
        res.initial_quality = cfg.quality.source_mg_l / 1000.0
    for _, tank in wn.tanks():
        tank.initial_quality = cfg.quality.tank_initial_mg_l / 1000.0


def graph(wn: wntr.network.WaterNetworkModel) -> nx.Graph:
    """Undirected graph weighted by pipe length (pumps and valves get length 1 m)."""
    g = nx.Graph()
    for name, link in wn.links():
        length = getattr(link, "length", 1.0)
        g.add_edge(link.start_node_name, link.end_node_name, weight=float(length), link=name)
    return g


def coordinates(wn: wntr.network.WaterNetworkModel) -> dict[str, tuple[float, float]]:
    return {name: (float(n.coordinates[0]), float(n.coordinates[1])) for name, n in wn.nodes()}


def extent(wn: wntr.network.WaterNetworkModel) -> float:
    xy = np.array(list(coordinates(wn).values()))
    return float(np.hypot(*(xy.max(axis=0) - xy.min(axis=0))))
