"""Use of the maintenance log by the central analytics.

* A valve operation or pump maintenance record suppresses spatial alarms on the
  sensors it affects for ``suppress_h`` hours from the record time. The affected
  sensors are those at the ``n_near`` stations closest (shortest pipe-length path)
  to the operated asset; for a pump, also every sensor of its own station.
* Recalibration and replacement records reset the chlorine drift CUSUM
  (flowmine.central.chlorine) and mark a step in the sensor's series as explained.
"""

from __future__ import annotations

import networkx as nx
import numpy as np
import pandas as pd
import wntr

from flowmine.testbed.sensors import Sensor

OPERATIONS = ("valve_operation", "pump_maintenance")


def affected_sensors(
    wn: wntr.network.WaterNetworkModel,
    g: nx.Graph,
    asset: str,
    sensors: list[Sensor],
    n_near: int = 4,
) -> list[str]:
    link = wn.get_link(asset)
    ends = [link.start_node_name, link.end_node_name]
    dist: dict[str, float] = {}
    for e in ends:
        for n, d in nx.single_source_dijkstra_path_length(g, e, weight="weight").items():
            dist[n] = min(d, dist.get(n, np.inf))

    def where(s: Sensor) -> str:
        if s.element_type in ("pipe", "pump"):
            return str(wn.get_link(s.element).start_node_name)
        return s.element

    by_station: dict[str, float] = {}
    for s in sensors:
        by_station[s.station] = min(by_station.get(s.station, np.inf), dist.get(where(s), np.inf))
    near = sorted(by_station, key=lambda st: by_station[st])[:n_near]
    out = {s.sensor_id for s in sensors if s.station in near}
    out |= {s.sensor_id for s in sensors if s.element == asset}
    return sorted(out)


def suppression_mask(
    times: np.ndarray,
    streams: list[str],
    log: pd.DataFrame,
    affected: dict[str, list[str]],
    hours: float,
) -> np.ndarray:
    """(n, m) bool: True where an operation record suppresses spatial alarms."""
    mask = np.zeros((len(times), len(streams)), dtype=bool)
    ops = log[log.type.isin(OPERATIONS)]
    for _, r in ops.iterrows():
        rows = (times >= r.time_s) & (times < r.time_s + hours * 3600)
        for s in affected.get(str(r.asset), []):
            if s in streams:
                mask[rows, streams.index(s)] = True
    return mask
