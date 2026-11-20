"""Idempotent provisioning of every SensorThings entity the system uses.

Running ``provision`` twice creates nothing the second time: each entity is looked
up by name first. A Thing found by name gets its properties updated, so the element
mapping always matches the current configuration.

Stream names (Datastream names) are the keys the rest of the code
uses:

    P-15 / F-60 / L-1 / C-15   measured, one per sensor, on the station's Thing
    LAB-C-15                   laboratory free chlorine at the analyser's node
    MAINT-<station>            maintenance log records of that station's assets
    SIM-<sensor>, RES-<sensor> twin: simulated value and residual (measured - simulated)
    VIRT-<node>                twin: simulated pressure at an unmonitored node
    WEATHER                    hourly air temperature (external source)
    EVENTS                     events raised by the central analytics
    LEAK-CANDIDATES            twin: top-5 leak candidate nodes with scores (JSON result)
    UPLINK-STATS               byte counters per station and uplink policy (demo)
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

import networkx as nx

from flowmine.config import Config
from flowmine.sta import mapping
from flowmine.sta.client import StaClient
from flowmine.testbed import network
from flowmine.testbed.sensors import Sensor, StationSpec

log = logging.getLogger(__name__)


@dataclass
class StaMap:
    things: dict[str, int] = field(default_factory=dict)
    datastreams: dict[str, int] = field(default_factory=dict)
    sensors: dict[str, int] = field(default_factory=dict)
    observed_properties: dict[str, int] = field(default_factory=dict)

    def measured(self, sensor_ids: list[str]) -> dict[str, int]:
        return {s: self.datastreams[s] for s in sensor_ids}


def dma_of(cfg: Config) -> dict[str, str]:
    """Supply zone of each node: the reservoir it is hydraulically closest to."""
    wn = network.load_network(cfg.network.inp)
    g = network.graph(wn)
    dist = {
        r: nx.single_source_dijkstra_path_length(g, r, weight="weight")
        for r in wn.reservoir_name_list
    }
    return {
        n: "DMA-" + min(dist, key=lambda r: dist[r].get(n, float("inf"))) for n in wn.node_name_list
    }


def thing_body(spec: StationSpec, sensors: list[Sensor], dma: str) -> dict[str, object]:
    return {
        "name": spec.station_id,
        "description": f"Edge station ({spec.station_class}) at EPANET node {spec.node}",
        "properties": {
            "station_class": spec.station_class,
            "station_number": spec.number,
            "dma": dma,
            "epanet_node": spec.node,
            "tank_area_m2": spec.tank_area_m2,
            "inflow_sign": spec.inflow_sign,
            "sensors": [
                {
                    "sensor_id": s.sensor_id,
                    "kind": s.kind,
                    "local_id": s.local_id,
                    "epanet_element": s.element,
                    "epanet_type": s.element_type,
                    "period_s": s.period_s,
                    "resolution": s.resolution,
                }
                for s in sensors
            ],
        },
    }


def provision(
    client: StaClient,
    cfg: Config,
    sensors: list[Sensor],
    stations: list[StationSpec],
    virtual_nodes: Sequence[str] = (),
) -> StaMap:
    m = StaMap()
    wn = network.load_network(cfg.network.inp)
    xy = network.coordinates(wn)
    dma = dma_of(cfg)
    for key in mapping.SENSORS:
        m.sensors[key] = client.ensure("Sensors", mapping.sensor_body(key))
    for key in mapping.OBSERVED_PROPERTIES:
        m.observed_properties[key] = client.ensure(
            "ObservedProperties", mapping.observed_property_body(key)
        )
    by_station = {
        st.station_id: [s for s in sensors if s.station == st.station_id] for st in stations
    }

    def thing(name: str, body: dict[str, object], node: str) -> int:
        found = client.find_by_name("Things", name)
        if found is not None:
            if found.get("properties") != body.get("properties"):
                client.patch(f"Things({found['@iot.id']})", {"properties": body["properties"]})
            return int(found["@iot.id"])
        body = dict(body)
        body["Locations"] = [mapping.location_body(f"node {node}", *xy[node])]
        return client.create("Things", body)

    def stream(
        name: str, kind: str, role: str, thing_id: int, sensor_key: str, props: dict[str, object]
    ) -> None:
        m.datastreams[name] = client.ensure(
            "Datastreams",
            mapping.datastream_body(
                name,
                kind,
                role,
                thing_id,
                m.sensors[sensor_key],
                m.observed_properties[kind],
                props,
            ),
        )

    for st in stations:
        tid = thing(st.station_id, thing_body(st, by_station[st.station_id], dma[st.node]), st.node)
        m.things[st.station_id] = tid
        for s in by_station[st.station_id]:
            props = {
                "sensor_id": s.sensor_id,
                "station": st.station_id,
                "epanet_element": s.element,
                "epanet_type": s.element_type,
                "local_id": s.local_id,
                "period_s": s.period_s,
                "resolution": s.resolution,
            }
            stream(s.sensor_id, s.kind, "measured", tid, s.kind, props)
            stream(f"SIM-{s.sensor_id}", s.kind, "simulated", tid, "model", props)
            stream(f"RES-{s.sensor_id}", s.kind, "residual", tid, "model", props)
            if s.kind == "chlorine":
                stream(
                    f"LAB-{s.sensor_id}",
                    "chlorine",
                    "laboratory",
                    tid,
                    "laboratory",
                    {**props, "sampling_point": s.element},
                )
        stream(
            f"MAINT-{st.station_id}",
            "maintenance",
            "maintenance",
            tid,
            "maintenance_log",
            {"station": st.station_id},
        )
    for node in virtual_nodes:
        name = f"VN-{node}"
        tid = thing(
            name,
            {
                "name": name,
                "description": f"Virtual sensor at EPANET node {node}",
                "properties": {
                    "station_class": "virtual",
                    "epanet_node": node,
                    "dma": dma[node],
                    "sensors": [],
                },
            },
            node,
        )
        m.things[name] = tid
        stream(
            f"VIRT-{node}",
            "pressure",
            "virtual",
            tid,
            "model",
            {"epanet_element": node, "epanet_type": "junction"},
        )
    src = wn.reservoir_name_list[0]
    tid = thing(
        "External",
        {
            "name": "External",
            "description": "External data sources",
            "properties": {"station_class": "external"},
        },
        src,
    )
    m.things["External"] = tid
    stream("WEATHER", "air_temperature", "external", tid, "weather", {"period_s": 3600})
    tid = thing(
        "Central",
        {
            "name": "Central",
            "description": "Central analytics",
            "properties": {"station_class": "central"},
        },
        src,
    )
    m.things["Central"] = tid
    stream("EVENTS", "event", "event", tid, "analytics", {})
    stream("UPLINK-STATS", "event", "uplink_statistics", tid, "analytics", {})
    stream("LEAK-CANDIDATES", "leak_candidates", "leak_candidates", tid, "model", {"k": 5})
    log.info("provisioned %d things, %d datastreams", len(m.things), len(m.datastreams))
    return m


def load_map(client: StaClient) -> StaMap:
    """Rebuild the name -> id map from what the server holds."""
    m = StaMap()
    for t in client.iterate("Things", {"$select": "@iot.id,name", "$top": 1000}):
        m.things[t["name"]] = int(t["@iot.id"])
    for d in client.iterate("Datastreams", {"$select": "@iot.id,name", "$top": 1000}):
        m.datastreams[d["name"]] = int(d["@iot.id"])
    for s in client.iterate("Sensors", {"$top": 1000}):
        m.sensors[s["properties"]["flowmine:key"]] = int(s["@iot.id"])
    for o in client.iterate("ObservedProperties", {"$top": 1000}):
        m.observed_properties[o["properties"]["flowmine:key"]] = int(o["@iot.id"])
    return m
