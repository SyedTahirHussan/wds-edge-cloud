"""``flowmine edge run``: one station as its own process, for the live demo.

The station loads the active model package of each of its sensors from the registry
(a shared directory stands in for the downlink), replays its observed samples at an
accelerated rate, and posts its binary frames to the gateway over HTTP. A failed post
leaves the frame in the station's outbox; it goes out with the next successful tick.
Next to the policy it actually uses, the station feeds the same records into the
other policies' counters and reports all byte counts to the gateway, so the
dashboard can compare policies on the same data.
"""

from __future__ import annotations

import contextlib
import logging
import time
from pathlib import Path

import httpx

from flowmine.config import Config
from flowmine.edge import replay
from flowmine.edge.models import PackagedModels
from flowmine.edge.node import EdgeNode
from flowmine.edge.store import LocalStore
from flowmine.edge.uplink import POLICIES, Policy, make_policy
from flowmine.hybrid.registry import ModelRegistry
from flowmine.testbed import build

log = logging.getLogger(__name__)


class HttpLink:
    """Posts frames to the gateway; ``up`` turns False when the gateway is unreachable.

    Connection errors and 5xx answers raise ConnectionError, so the frame stays in the
    outbox and is retried. A 4xx answer means the gateway will never accept the frame;
    it is logged, counted in ``rejected`` and dropped, so it cannot block the frames
    queued behind it.
    """

    def __init__(self, url: str) -> None:
        self.url = url.rstrip("/")
        self.http = httpx.Client(timeout=10.0)
        self.up = True
        self.rejected = 0

    def send(self, station: str, frame: bytes) -> None:
        try:
            r = self.http.post(
                f"{self.url}/uplink/{station}",
                content=frame,
                headers={"Content-Type": "application/octet-stream"},
            )
        except httpx.HTTPError as exc:
            self.up = False
            raise ConnectionError(str(exc)) from exc
        if 400 <= r.status_code < 500:
            self.rejected += 1
            log.error("gateway rejected a frame from %s: %d %s", station, r.status_code, r.text)
            return
        if r.status_code >= 500:
            self.up = False
            raise ConnectionError(f"gateway answered {r.status_code}")
        self.up = True


def load_packages(registry: ModelRegistry, sensor_ids: list[str]) -> dict[str, PackagedModels]:
    out = {}
    for sid in sensor_ids:
        e = registry.active(sid)
        if e is None:
            raise RuntimeError(f"no active model for {sid} in {registry.root}")
        out[sid] = PackagedModels(registry.blob(e))
    return out


def shadow_policies(
    node: EdgeNode, cfg: Config, bundles: dict[str, PackagedModels]
) -> dict[str, Policy]:
    sens = list(node.sensors.values())
    scales = {s.local_id: s.resolution for s in sens}
    ds = {s.local_id: 0 for s in sens}
    dual = {s.local_id: bundles[s.sensor_id].hw_copy() for s in sens}
    eps = {s.local_id: cfg.sensors[s.kind].epsilon for s in sens}
    return {
        p: make_policy(
            p,
            node.spec.number,
            scales,
            ds,
            node.policy.start,
            cfg.edge.summary_s,
            cfg.edge.max_payload,
            models=dual,
            eps=eps,
            heartbeat_s=cfg.edge.heartbeat_s,
            window_s=cfg.edge.summary_s,
        )
        for p in POLICIES
        if p != node.policy.name
    }


def run(
    cfg: Config,
    seed: int,
    station: str,
    gateway: str,
    registry_dir: Path,
    t0: int,
    t1: int,
    speed: float,
    policy: str,
    store_dir: Path,
) -> int:
    tb = build.ensure(cfg, seed)
    spec = next(s for s in tb.stations if s.station_id == station)
    registry = ModelRegistry(registry_dir)
    bundles = load_packages(registry, list(spec.sensors))
    link = HttpLink(gateway)
    store_dir.mkdir(parents=True, exist_ok=True)
    store = LocalStore(store_dir / f"{station}.sqlite", int(cfg.edge.retention_days * 86400))
    node = replay.make_node(tb, spec, bundles, policy, link=link, store=store)
    node.keep_records = False
    shadows = shadow_policies(node, cfg, bundles)
    http = httpx.Client(timeout=10.0)
    period_s = min(tb.sensor(s).period_s for s in spec.sensors)
    last_stats = -1
    for t, sid, v in replay.station_samples(tb, spec, t0, t1):
        for r in node.ingest(sid, t, v):
            for p in shadows.values():
                p.offer(r)
        for p in shadows.values():
            p.due(t)
        link.up = True
        try:
            node.tick(t)
        except ConnectionError:
            log.warning("%s: gateway unreachable, %d frames queued", station, node.backlog())
        if t // 3600 != last_stats:
            last_stats = t // 3600
            stats = {node.policy.name: vars(node.policy.stats) | {"latencies": None}}
            stats |= {k: vars(p.stats) | {"latencies": None} for k, p in shadows.items()}
            with contextlib.suppress(httpx.HTTPError):
                http.post(
                    f"{gateway.rstrip('/')}/stats/{station}", json={"t": t, "policies": stats}
                )
        time.sleep(period_s / speed / max(len(spec.sensors), 1))
    node.flush_station()
    node.tick(t1 + node.policy.batch_s)
    if node.backlog() or link.rejected:
        log.error(
            "%s: %d frames still queued, %d rejected by the gateway",
            station,
            node.backlog(),
            link.rejected,
        )
        return 1
    return 0
