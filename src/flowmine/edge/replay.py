"""Replay testbed observations through edge nodes (experiments, demo, tests)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import datetime

import numpy as np

from flowmine.config import Config
from flowmine.edge.models import SensorModels
from flowmine.edge.node import EdgeNode, Link
from flowmine.edge.store import LocalStore
from flowmine.testbed.build import Testbed
from flowmine.testbed.sensors import StationSpec


def station_samples(
    tb: Testbed, spec: StationSpec, t0: int, t1: int
) -> Iterator[tuple[int, str, float | None]]:
    """Observed samples of one station in time order (flows before levels at equal t)."""
    obs = tb.observed
    sub = obs[obs.sensor_id.isin(spec.sensors) & (obs.time_s >= t0) & (obs.time_s < t1)]
    kind = {s.sensor_id: s.kind for s in tb.sensors}
    order = sub.assign(rank=sub.sensor_id.map(lambda s: 1 if kind[s] == "level" else 0))
    order = order.sort_values(["time_s", "rank", "sensor_id"], kind="stable")
    for t, sid, v in zip(
        order.time_s.to_numpy(), order.sensor_id.to_numpy(), order.value.to_numpy(), strict=True
    ):
        yield int(t), str(sid), (None if np.isnan(v) else float(v))


def datastream_ids(tb: Testbed) -> dict[str, int]:
    """Stable small integers standing in for SensorThings Datastream ids."""
    return {s.sensor_id: i + 1 for i, s in enumerate(tb.sensors)}


def start_time(cfg: Config) -> datetime:
    return datetime.fromisoformat(cfg.network.start)


def make_node(
    tb: Testbed,
    spec: StationSpec,
    bundles: dict[str, SensorModels],
    policy: str,
    forecaster: str = "hw",
    link: Link | None = None,
    store: LocalStore | None = None,
) -> EdgeNode:
    sens = [tb.sensor(s) for s in spec.sensors]
    return EdgeNode(
        spec,
        sens,
        {s: bundles[s] for s in spec.sensors},
        tb.cfg,
        policy,
        start_time(tb.cfg),
        datastream_ids(tb),
        forecaster,
        store,
        link,
    )


def run_station(
    node: EdgeNode, tb: Testbed, t0: int, t1: int, link_up: Callable[[int], bool] | None = None
) -> EdgeNode:
    """Feed [t0, t1) through ``node``; ``link_up(t)`` can take the link down (outages)."""
    for t, sid, v in station_samples(tb, node.spec, t0, t1):
        if link_up is not None:
            node.link.up = link_up(t)
        node.tick(t)
        node.ingest(sid, t, v)
    node.flush_station()
    if link_up is not None:
        node.link.up = link_up(t1)
    node.tick(t1 + node.policy.batch_s)
    return node
