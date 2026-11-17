"""E3 (communication cost) and E4 (resilience to a cloud or link outage).

E3: every station runs over the test week with each uplink policy. Bytes are counted
for the binary frames and for the SensorThings JSON batch bodies. Detection latency
is the time from a sample to the flush that delivers it, over samples carrying a
fault label (for ``raw`` the cloud can only start validating on arrival). For
``delta`` the cloud reconstructs every slot with its copy of the forecaster, and the
error against the edge-validated value and against the truth is reported for several
epsilons (multiples of the per-class epsilon in the configuration). Payload use is
given against the 51-byte application payload of LoRaWAN EU868 at DR0-DR2 (LoRaWAN
Regional Parameters RP002-1.0.4, table 12, repeater compatible).

E4: the link is down for ``outage_hours`` from ``outage_start_h`` into the test week.
Stations keep validating and storing; frames wait in the outbox and are sent after
the outage at ``backfill_frames_per_hour`` (an assumed uplink rate, not a measured
LoRaWAN airtime budget). A centralised design (raw policy, validation in the cloud)
can only flag a fault in the window once its samples have arrived, so it flags none
at the station during the outage. The outbox has no size limit and the station keeps
samples longer than the longest outage, so neither design loses a sample here; the
difference is what is known during the outage and how long the backlog takes.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable

import numpy as np
import pandas as pd

from flowmine.edge import replay
from flowmine.edge.core import codec, quality
from flowmine.edge.core.dualpred import DeltaReceiver
from flowmine.edge.node import EdgeNode, MemoryLink
from flowmine.experiments.context import SeedContext
from flowmine.testbed.faults import GAPS

LORA_EU868_DR0_PAYLOAD = 51


def _run_policy(
    ctx: SeedContext,
    policy: str,
    eps_factor: float = 1.0,
    link_up: Callable[[int], bool] | None = None,
) -> tuple[list[EdgeNode], int]:
    tb = ctx.tb
    t0, t1 = ctx.periods["test"]
    cfg = tb.cfg
    if eps_factor != 1.0:
        sensors = {
            k: dataclasses.replace(v, epsilon=v.epsilon * eps_factor)
            for k, v in cfg.sensors.items()
        }
        cfg = dataclasses.replace(cfg, sensors=sensors)
        tb = dataclasses.replace(tb, cfg=cfg)
    nodes = []
    for spec in tb.stations:
        node = replay.make_node(tb, spec, ctx.calibrated["hw"], policy, link=MemoryLink())
        replay.run_station(node, tb, t0, t1, link_up=link_up)
        nodes.append(node)
    return nodes, t1 - t0


def _fault_latency(ctx: SeedContext, nodes: list[EdgeNode]) -> float:
    lab = ctx.tb.labels
    faulty = set(
        map(
            tuple,
            lab[(lab.fault_type != "") & ~lab.fault_type.isin(GAPS)][
                ["sensor_id", "time_s"]
            ].to_numpy(),
        )
    )
    lat = []
    for node in nodes:
        by_local = node.by_local
        for fl_t, recs in node.flushes:
            for r in recs:
                if (by_local[r[0]], r[1]) in faulty:
                    lat.append(fl_t - r[1])
    return float(np.median(lat)) / 60.0 if lat else np.nan


def _delta_error(ctx: SeedContext, nodes: list[EdgeNode]) -> tuple[float, float]:
    truth = ctx.tb.truth.set_index(["sensor_id", "time_s"]).value
    wide = ctx.tb.wide("truth")
    errs_v, errs_t = [], []
    for node in nodes:
        spec = node.spec
        scales = {ctx.tb.sensor(s).local_id: ctx.tb.sensor(s).resolution for s in spec.sensors}
        got: dict[tuple[int, int], float] = {}
        assert isinstance(node.link, MemoryLink)
        for _, fr in node.link.frames:
            for rec in codec.decode(fr, scales)[2]:
                got[(rec[0], rec[1])] = rec[2]
        for sid in spec.sensors:
            s = ctx.tb.sensor(sid)
            rx = DeltaReceiver(ctx.calibrated["hw"][sid].hw_copy())
            span = max(float(wide[sid].quantile(0.95) - wide[sid].quantile(0.05)), 1e-9)
            for r in (r for r in node.records if r[0] == sid):
                est = rx.step(r[1], got.get((s.local_id, r[1])))
                if r[3] is None or est is None:
                    continue
                errs_v.append(abs(est - r[3]) / span)
                errs_t.append(abs(est - truth.loc[(sid, r[1])]) / span)
    return float(np.mean(errs_v)), float(np.mean(errs_t))


def e3(ctx: SeedContext) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    for policy in ("raw", "validated", "delta", "summary"):
        nodes, span_s = _run_policy(ctx, policy)
        days = span_s / 86400
        n_st = len(nodes)
        frames = sum(n.policy.stats.frames for n in nodes)
        binary = sum(n.policy.stats.binary_bytes for n in nodes)
        js = sum(n.policy.stats.json_bytes for n in nodes)
        rows.append(
            {
                "seed": ctx.seed,
                "policy": policy,
                "binary_bytes_per_station_day": binary / n_st / days,
                "json_bytes_per_station_day": js / n_st / days,
                "frames_per_station_day": frames / n_st / days,
                "mean_frame_fill": binary / frames / LORA_EU868_DR0_PAYLOAD if frames else np.nan,
                "fault_latency_min": _fault_latency(ctx, nodes),
            }
        )
    eps_rows = []
    for f in ctx.cfg.experiments.epsilons:
        nodes, span_s = _run_policy(ctx, "delta", f)
        ev, et = _delta_error(ctx, nodes)
        binary = sum(n.policy.stats.binary_bytes for n in nodes)
        eps_rows.append(
            {
                "seed": ctx.seed,
                "eps_factor": f,
                "binary_bytes_per_station_day": binary / len(nodes) / (span_s / 86400),
                "cloud_error_vs_edge": ev,
                "cloud_error_vs_truth": et,
            }
        )
    return pd.DataFrame(rows), pd.DataFrame(eps_rows)


def e4(ctx: SeedContext) -> pd.DataFrame:
    ex = ctx.cfg.experiments
    t0 = ctx.periods["test"][0]
    lab = ctx.tb.labels
    rows = []
    for hours in ex.outage_hours:
        a = t0 + int(ex.outage_start_h * 3600)
        b = a + int(hours * 3600)

        def up(t: int, a: int = a, b: int = b) -> bool:
            return not (a <= t < b)

        win = lab[
            (lab.time_s >= a)
            & (lab.time_s < b)
            & (lab.fault_type != "")
            & ~lab.fault_type.isin(GAPS)
        ]
        for arch, policy in (("edge_store_forward", "validated"), ("centralised_raw", "raw")):
            nodes, _ = _run_policy(ctx, policy, link_up=up)
            recs = pd.concat(
                [
                    pd.DataFrame(
                        n.records, columns=["sensor_id", "time_s", "raw", "value", "q", "score"]
                    )
                    for n in nodes
                ]
            )
            if policy == "raw":
                # No validation at the station: nothing is known until the backlog arrives.
                detected = 0
            else:
                m = win.merge(recs, on=["sensor_id", "time_s"], how="left")
                q = m.q.fillna(0).astype(int)
                flagged = (q & (quality.REJECTING | quality.CROSSCHECK)) != 0
                detected = int(m[flagged].fault_id.nunique())
            backfill_h = (
                sum(n.max_backlog for n in nodes) / len(nodes) / ex.backfill_frames_per_hour
            )
            rows.append(
                {
                    "seed": ctx.seed,
                    "outage_h": hours,
                    "architecture": arch,
                    "fault_events_in_window": int(win.fault_id.nunique()),
                    "events_flagged_during_outage": detected,
                    "mean_backlog_frames": sum(n.max_backlog for n in nodes) / len(nodes),
                    "backfill_h": backfill_h,
                    "cloud_up_to_date_after_h": hours + backfill_h,
                }
            )
    return pd.DataFrame(rows)
