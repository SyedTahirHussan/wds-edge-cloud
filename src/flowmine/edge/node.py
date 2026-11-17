"""Edge node: one station's runtime on CPython (the deployment target would be MicroPython).

The node runs the core pipeline on each incoming sample, keeps every record in its
local SQLite store, hands records to the uplink policy and sends the resulting frames
over a link. When the link is down frames go to the outbox and are replayed in order
once it is back (store-and-forward). New model versions arrive as hashed packages;
the node verifies the hash, warms the new forecaster on its own stored history,
switches atomically and keeps the previous version for rollback.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from flowmine.config import Config
from flowmine.edge.core import quality
from flowmine.edge.core.holtwinters import HoltWinters
from flowmine.edge.core.mlp import MLPForecaster
from flowmine.edge.core.pipeline import SensorPipeline
from flowmine.edge.core.snaive import SeasonalNaive
from flowmine.edge.core.station import Station
from flowmine.edge.models import PackagedModels, SensorModels
from flowmine.edge.store import LocalStore
from flowmine.edge.uplink import DeltaPolicy, EdgeRecord, Flush, Policy, make_policy
from flowmine.testbed.sensors import Sensor, StationSpec

log = logging.getLogger(__name__)
# The core classes are untyped (MicroPython code, excluded from mypy).
Forecaster = Any


class Link(Protocol):
    up: bool

    def send(self, station: str, frame: bytes) -> None: ...


class MemoryLink:
    """Collects frames in memory (experiments and tests)."""

    def __init__(self) -> None:
        self.up = True
        self.frames: list[tuple[str, bytes]] = []

    def send(self, station: str, frame: bytes) -> None:
        self.frames.append((station, frame))


def model_package(
    sensor_id: str,
    version: int,
    model: Forecaster,
    mlp_bytes: bytes | None = None,
    meta: dict[str, Any] | None = None,
) -> tuple[bytes, str]:
    """Serialise a forecaster (and optionally the pipeline parameters) for distribution;
    returns (blob, sha256 hex)."""
    body: dict[str, Any] = {
        "sensor": sensor_id,
        "version": version,
        "kind": model.kind,
        "state": model.state(),
    }
    if meta is not None:
        body["meta"] = meta
    if mlp_bytes is not None:
        body["weights"] = base64.b64encode(mlp_bytes).decode()
    blob = json.dumps(body, separators=(",", ":")).encode()
    return blob, hashlib.sha256(blob).hexdigest()


def load_package(blob: bytes) -> tuple[str, int, Forecaster]:
    body = json.loads(blob)
    st = body["state"]
    model: Forecaster
    if body["kind"] == "hw":
        model = HoltWinters(st["period"], st["s1"], st["s2"], 0.0, 0.0, 0.0, 0.0)
    elif body["kind"] == "mlp":
        model = MLPForecaster(base64.b64decode(body["weights"]))
    else:
        model = SeasonalNaive(st["period"], st["season"])
    model.load(st)
    return body["sensor"], int(body["version"]), model


@dataclass
class Installed:
    version: int
    model: Forecaster


class EdgeNode:
    def __init__(
        self,
        spec: StationSpec,
        sensors: list[Sensor],
        bundles: Mapping[str, SensorModels | PackagedModels],
        cfg: Config,
        policy: str,
        start: datetime,
        ds_ids: dict[str, int],
        forecaster: str = "hw",
        store: LocalStore | None = None,
        link: Link | None = None,
        model_version: int = 1,
    ) -> None:
        self.spec = spec
        self.sensors = {s.sensor_id: s for s in sensors}
        self.local = {s.sensor_id: s.local_id for s in sensors}
        self.by_local = {s.local_id: s.sensor_id for s in sensors}
        self.store = store or LocalStore(retention_s=int(cfg.edge.retention_days * 86400))
        self.link: Link = link or MemoryLink()
        self.current: dict[str, Installed] = {}
        self.previous: dict[str, Installed] = {}
        pipes = {}
        for sid, b in bundles.items():
            model = b.forecaster(forecaster)
            self.current[sid] = Installed(model_version, model)
            pipes[self.local[sid]] = SensorPipeline(b.meta, model, model_version)
        tank = None
        if spec.station_class == "tank" and spec.tank_area_m2:
            lvl = next(s for s in sensors if s.kind == "level")
            flw = next(s for s in sensors if s.kind == "flow")
            tank = {
                "level": lvl.local_id,
                "flow": flw.local_id,
                "area_m2": spec.tank_area_m2,
                "tol_m": cfg.edge.tank_tol_m,
                "sign": spec.inflow_sign,
            }
        self.core = Station(spec.number, pipes, tank)
        scales = {s.local_id: s.resolution for s in sensors}
        dual = {self.local[sid]: b.hw_copy() for sid, b in bundles.items()}
        eps = {s.local_id: cfg.sensors[s.kind].epsilon for s in sensors}
        self.policy: Policy = make_policy(
            policy,
            spec.number,
            scales,
            {s.local_id: ds_ids[s.sensor_id] for s in sensors},
            start,
            cfg.edge.summary_s,
            cfg.edge.max_payload,
            models=dual,
            eps=eps,
            heartbeat_s=cfg.edge.heartbeat_s,
            window_s=cfg.edge.summary_s,
        )
        self.policy.model_version = model_version
        self.records: list[tuple[str, int, float | None, float | None, int, float]] = []
        self.keep_records = True
        self.flushes: list[tuple[int, list[tuple[Any, ...]]]] = []
        self._pending_records: list[tuple[Any, ...]] = []
        self.max_backlog = 0

    def ingest(self, sensor_id: str, t: int, x: float | None) -> list[EdgeRecord]:
        recs = self.core.process(self.local[sensor_id], t, x)
        self._accept(recs)
        return recs

    def flush_station(self) -> list[EdgeRecord]:
        recs = self.core.flush()
        self._accept(recs)
        return recs

    def _accept(self, recs: list[EdgeRecord]) -> None:
        if not recs:
            return
        rows = [(self.by_local[r[0]], r[1], r[2], r[3], r[4], r[5]) for r in recs]
        self.store.put(rows)
        if self.keep_records:
            self.records.extend(rows)
        for r in recs:
            self.policy.offer(r)

    def tick(self, now: int) -> Flush | None:
        """Flush the policy if due and push frames (or queue them when the link is down)."""
        fl = self.policy.due(now)
        if fl is not None:
            for fr in fl.frames:
                self.store.enqueue(now, fr)
            self._pending_records.extend(fl.records)
        if not self.link.up:
            self.max_backlog = max(self.max_backlog, self.backlog())
        if self.link.up:
            sent = self.store.outbox()
            for seq, _created, frame in sent:
                self.link.send(self.spec.station_id, frame)
                self.store.ack(seq)
            if sent and self.keep_records:
                self.flushes.append((now, self._pending_records))
                self._pending_records = []
        self.store.commit()
        return fl

    def backlog(self) -> int:
        return len(self.store.outbox())

    def install_model(self, blob: bytes, sha256: str) -> bool:
        """Verify, warm on local history, and switch to a new model version."""
        if hashlib.sha256(blob).hexdigest() != sha256:
            log.warning("station %s: model hash mismatch, rejected", self.spec.station_id)
            return False
        sid, version, model = load_package(blob)
        if sid not in self.current:
            return False
        if model.k is not None:
            period = self.sensors[sid].period_s
            t0 = (model.k + 1) * period
            last = self.core.pipelines[self.local[sid]].grid.last_slot
            t1 = (last + 1) * period if last is not None else t0
            for t, _raw, value, q in self.store.history(sid, t0, t1):
                ok = value is not None and not (q & (quality.REJECTING | quality.MISSING))
                model.update(value if ok else None, t)
        self.previous[sid] = self.current[sid]
        self.current[sid] = Installed(version, model)
        self.core.pipelines[self.local[sid]].set_model(model, version)
        self.policy.model_version = version
        self._sync_delta(sid, model)
        return True

    def _sync_delta(self, sensor_id: str, model: object) -> None:
        """Give the send-on-delta sender its own copy of the newly active model.

        The cloud's receiver switches with the version in the frame, so the sender must
        switch too. The next value is sent unconditionally, so the cloud has a measured
        anchor right after the switch.
        """
        if not isinstance(self.policy, DeltaPolicy) or not isinstance(model, HoltWinters):
            return
        sender = self.policy.senders[self.local[sensor_id]]
        sender.model = copy.deepcopy(model)
        sender.last_tx = None

    def rollback(self, sensor_id: str) -> bool:
        prev = self.previous.pop(sensor_id, None)
        if prev is None:
            return False
        self.current[sensor_id] = prev
        self.core.pipelines[self.local[sensor_id]].set_model(prev.model, prev.version)
        self.policy.model_version = prev.version
        self._sync_delta(sensor_id, prev.model)
        return True

    def model_version(self, sensor_id: str) -> int:
        return self.current[sensor_id].version
