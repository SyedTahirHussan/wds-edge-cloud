"""Uplink policies and their byte accounting in two encodings.

Policies (what leaves the station):

* ``raw``: every raw sample, unvalidated (the cloud does all validation);
* ``validated``: every record with its quality code (reconstructions included);
* ``delta``: send-on-delta with dual prediction; flagged records are always sent;
* ``summary``: 15-minute mean/min/max with OR-ed flags, plus every flagged record.

Records are buffered and flushed every ``batch_s`` seconds; a record that a test
rejected (or a level shift) triggers an immediate flush in the validated, delta and
summary policies so that events are not delayed by the batching. Each flush is
encoded twice: as binary frames (``flowmine.edge.core.codec``) limited to
``max_payload`` bytes, and as one SensorThings JSON batch request body.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from flowmine.edge.core import codec, quality
from flowmine.edge.core.dualpred import DeltaSender
from flowmine.edge.core.holtwinters import HoltWinters
from flowmine.sta import mapping

# (local sensor id, t, raw, value, quality code, residual score), as returned by core.Station
EdgeRecord = tuple[int, int, float | None, float | None, int, float]
URGENT = quality.REJECTING | quality.SHIFT
POLICIES = ("raw", "validated", "delta", "summary")


@dataclass
class Flush:
    t: int
    records: list[tuple[Any, ...]]
    frames: list[bytes]
    json_bytes: int

    @property
    def binary_bytes(self) -> int:
        return sum(len(f) for f in self.frames)


@dataclass
class UplinkStats:
    frames: int = 0
    binary_bytes: int = 0
    json_bytes: int = 0
    records: int = 0
    latencies: list[int] = field(default_factory=list)

    def add(self, fl: Flush) -> None:
        self.frames += len(fl.frames)
        self.binary_bytes += fl.binary_bytes
        self.json_bytes += fl.json_bytes
        self.records += len(fl.records)


def batch_json(
    start: datetime, records: list[tuple[Any, ...]], ds_ids: dict[int, int], window_s: int = 900
) -> str:
    """The FROST JSON batch body that would carry these records."""
    reqs = []
    for i, r in enumerate(records):
        if len(r) == 7:
            body = mapping.observation_body(start, r[1], r[2], r[5], datastream_id=ds_ids[r[0]])
            body["phenomenonTime"] = (
                mapping.iso(start, r[1]) + "/" + mapping.iso(start, r[1] + window_s)
            )
            body["parameters"]["min"] = r[3]
            body["parameters"]["max"] = r[4]
        else:
            body = mapping.observation_body(start, r[1], r[2], r[3], datastream_id=ds_ids[r[0]])
        reqs.append({"id": str(i), "method": "post", "url": "Observations", "body": body})
    return json.dumps({"requests": reqs}, separators=(",", ":"))


class Policy:
    name = "base"

    def __init__(
        self,
        station: int,
        scales: dict[int, float],
        ds_ids: dict[int, int],
        start: datetime,
        batch_s: int,
        max_payload: int,
    ) -> None:
        self.station = station
        self.scales = scales
        self.ds_ids = ds_ids
        self.start = start
        self.batch_s = batch_s
        self.max_payload = max_payload
        self.buffer: list[tuple[Any, ...]] = []
        self.next_flush: int | None = None
        self.urgent = False
        self.model_version = 0
        self.stats = UplinkStats()
        self.window_s = 900  # summary window; SummaryPolicy sets its own

    def select(self, rec: EdgeRecord) -> list[tuple[Any, ...]]:
        """Codec records ``(local, t, value, quality)`` to transmit for this edge record."""
        raise NotImplementedError

    def offer(self, rec: EdgeRecord) -> None:
        if self.next_flush is None:
            self.next_flush = (rec[1] // self.batch_s + 1) * self.batch_s
        out = self.select(rec)
        self.buffer.extend(out)
        if out and rec[4] & URGENT and self.name != "raw":
            self.urgent = True

    def due(self, now: int) -> Flush | None:
        if not self.buffer:
            return None
        if not self.urgent and (self.next_flush is None or now < self.next_flush):
            return None
        recs = sorted(self.buffer, key=lambda r: (r[1], r[0]))
        self.buffer = []
        self.urgent = False
        self.next_flush = (now // self.batch_s + 1) * self.batch_s
        frames = codec.batch(self.station, self.model_version, recs, self.scales, self.max_payload)
        fl = Flush(
            now,
            recs,
            frames,
            len(batch_json(self.start, recs, self.ds_ids, self.window_s).encode()),
        )
        self.stats.add(fl)
        self.stats.latencies.extend(now - r[1] for r in recs)
        return fl


class RawPolicy(Policy):
    name = "raw"

    def select(self, rec: EdgeRecord) -> list[tuple[Any, ...]]:
        local, t, raw = rec[0], rec[1], rec[2]
        return [] if raw is None else [(local, t, raw, 0)]


class ValidatedPolicy(Policy):
    name = "validated"

    def select(self, rec: EdgeRecord) -> list[tuple[Any, ...]]:
        local, t, _raw, value, q = rec[:5]
        return [] if value is None else [(local, t, value, q)]


class DeltaPolicy(Policy):
    name = "delta"

    def __init__(
        self,
        *args: Any,
        models: dict[int, HoltWinters],
        eps: dict[int, float],
        heartbeat_s: int,
        **kw: Any,
    ) -> None:
        super().__init__(*args, **kw)
        self.senders = {
            k: DeltaSender(m, eps[k], heartbeat_s, self.scales[k]) for k, m in models.items()
        }

    def select(self, rec: EdgeRecord) -> list[tuple[Any, ...]]:
        local, t, _raw, value, q = rec[:5]
        sent, held = self.senders[local].offer(t, value, force=quality.flags(q) != 0)
        return [(local, t, held, q)] if sent else []


class SummaryPolicy(Policy):
    name = "summary"

    def __init__(self, *args: Any, window_s: int, **kw: Any) -> None:
        super().__init__(*args, **kw)
        self.window_s = window_s
        self.acc: dict[int, tuple[int, list[float], int]] = {}

    def select(self, rec: EdgeRecord) -> list[tuple[Any, ...]]:
        local, t, _raw, value, q = rec[:5]
        out: list[tuple[Any, ...]] = []
        w0 = t // self.window_s * self.window_s
        cur = self.acc.get(local)
        if cur is not None and cur[0] != w0:
            out.extend(self._close(local))
            cur = None
        if cur is None:
            cur = (w0, [], 0)
        vals = cur[1] + ([value] if value is not None else [])
        self.acc[local] = (w0, vals, cur[2] | quality.flags(q))
        if q & URGENT and value is not None:
            out.append((local, t, value, q))
        return out

    def _close(self, local: int) -> list[tuple[Any, ...]]:
        w0, vals, flags = self.acc.pop(local)
        if not vals:
            return []
        return [(local, w0, sum(vals) / len(vals), min(vals), max(vals), flags, "s")]


def make_policy(
    name: str,
    station: int,
    scales: dict[int, float],
    ds_ids: dict[int, int],
    start: datetime,
    batch_s: int,
    max_payload: int,
    models: dict[int, HoltWinters] | None = None,
    eps: dict[int, float] | None = None,
    heartbeat_s: int = 3600,
    window_s: int = 900,
) -> Policy:
    args = (station, scales, ds_ids, start, batch_s, max_payload)
    if name == "raw":
        return RawPolicy(*args)
    if name == "validated":
        return ValidatedPolicy(*args)
    if name == "delta":
        assert models is not None and eps is not None
        return DeltaPolicy(*args, models=models, eps=eps, heartbeat_s=heartbeat_s)
    if name == "summary":
        return SummaryPolicy(*args, window_s=window_s)
    raise ValueError(name)
