"""Sensor placement, sensor metadata and the grouping of sensors into edge stations.

Placement is greedy farthest-point on the pipe-length-weighted graph: starting from
the pump discharge junctions, each new pressure logger goes to the junction whose
shortest-path distance to the nearest existing logger is largest. Chlorine
analysers are placed the same way among the pressure-logger sites, so those sites
become two-sensor stations.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import networkx as nx
import wntr

from flowmine.config import Config


@dataclass(frozen=True)
class Sensor:
    sensor_id: str
    kind: str  # pressure | flow | level | chlorine
    element: str  # EPANET node or link id
    element_type: str  # junction | tank | pipe | pump
    station: str
    local_id: int  # index inside its station (u8 on the wire)
    period_s: int
    resolution: float
    lo: float
    hi: float
    unit: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class StationSpec:
    station_id: str
    number: int  # u8 on the wire
    station_class: str  # pump | tank | logger
    node: str  # EPANET node where the station sits
    sensors: tuple[str, ...]
    tank_area_m2: float | None = None
    inflow_sign: float = 1.0  # +1 if positive meter flow fills the tank

    def to_dict(self) -> dict[str, object]:
        d = asdict(self)
        d["sensors"] = list(self.sensors)
        return d


UNITS = {"pressure": "m", "flow": "L/s", "level": "m", "chlorine": "mg/L"}


def greedy_far(g: nx.Graph, candidates: list[str], seeds: list[str], n: int) -> list[str]:
    """Greedy farthest-point selection of ``n`` nodes (``seeds`` count towards ``n``)."""
    chosen = list(seeds)
    dist = {c: float("inf") for c in candidates}
    for s in chosen:
        d = nx.single_source_dijkstra_path_length(g, s, weight="weight")
        for c in candidates:
            dist[c] = min(dist[c], d.get(c, float("inf")))
    while len(chosen) < n:
        best = max((c for c in candidates if c not in chosen), key=lambda c: (dist[c], c))
        chosen.append(best)
        d = nx.single_source_dijkstra_path_length(g, best, weight="weight")
        for c in candidates:
            dist[c] = min(dist[c], d.get(c, float("inf")))
    return chosen


def source_links(wn: wntr.network.WaterNetworkModel) -> list[str]:
    """Links leaving each reservoir (the source meters)."""
    out = []
    for res_name, _ in wn.reservoirs():
        out.extend(sorted(wn.get_links_for_node(res_name)))
    return out


def tank_links(wn: wntr.network.WaterNetworkModel) -> dict[str, str]:
    """For each tank with a single connecting link, that link (the inlet meter)."""
    out = {}
    for tank_name, _ in wn.tanks():
        links = wn.get_links_for_node(tank_name)
        if len(links) == 1:
            out[tank_name] = links[0]
    return out


def layout(
    wn: wntr.network.WaterNetworkModel, g: nx.Graph, cfg: Config
) -> tuple[list[Sensor], list[StationSpec]]:
    sc = cfg.sensors
    junctions = sorted(wn.junction_name_list)
    pumps = sorted(wn.pump_name_list)
    discharge = [wn.get_link(p).end_node_name for p in pumps]
    discharge = [d for d in discharge if d in junctions]
    pressure_nodes = greedy_far(g, junctions, discharge, cfg.layout.n_pressure)
    others = [n for n in pressure_nodes if n not in discharge]
    # others[0] is the logger farthest from the pumps; chlorine placement starts there.
    chlorine_nodes = greedy_far(g, others, others[:1], cfg.layout.n_chlorine)

    sensors: list[Sensor] = []
    stations: list[StationSpec] = []

    def add_sensor(
        kind: str,
        element: str,
        etype: str,
        station: str,
        local: int,
        lo: float | None = None,
        hi: float | None = None,
    ) -> str:
        c = sc[kind]
        sid = f"{kind[0].upper()}-{element}"
        sensors.append(
            Sensor(
                sid,
                kind,
                element,
                etype,
                station,
                local,
                c.period_s,
                c.resolution,
                c.lo if lo is None else lo,
                c.hi if hi is None else hi,
                UNITS[kind],
            )
        )
        return sid

    number = 0
    src = source_links(wn)
    for pump in pumps:
        link = wn.get_link(pump)
        meter = pump
        for s in src:
            sl = wn.get_link(s)
            if pump not in src and link.start_node_name in (sl.start_node_name, sl.end_node_name):
                meter = s
        st = f"PS-{pump}"
        ids = [add_sensor("flow", meter, wn.get_link(meter).link_type.lower(), st, 0)]
        node = link.end_node_name
        if node in pressure_nodes:
            ids.append(add_sensor("pressure", node, "junction", st, 1))
        stations.append(StationSpec(st, number, "pump", node, tuple(ids)))
        number += 1
    for tank_name, link_name in sorted(tank_links(wn).items()):
        tank = wn.get_node(tank_name)
        st = f"TK-{tank_name}"
        area = 3.141592653589793 * tank.diameter**2 / 4.0
        ids = [
            add_sensor(
                "level", tank_name, "tank", st, 0, tank.min_level - 0.5, tank.max_level + 0.5
            ),
            add_sensor("flow", link_name, "pipe", st, 1),
        ]
        sign = -1.0 if wn.get_link(link_name).start_node_name == tank_name else 1.0
        stations.append(StationSpec(st, number, "tank", tank_name, tuple(ids), area, sign))
        number += 1
    for node in sorted(others):
        st = f"LG-{node}"
        ids = [add_sensor("pressure", node, "junction", st, 0)]
        if node in chlorine_nodes:
            ids.append(add_sensor("chlorine", node, "junction", st, 1))
        stations.append(StationSpec(st, number, "logger", node, tuple(ids)))
        number += 1
    return sensors, stations
