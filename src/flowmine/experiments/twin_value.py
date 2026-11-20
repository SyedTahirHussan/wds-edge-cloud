"""E6: value of validation for the hydraulic model.

The bridge runs over the test week four times, fed with: the raw observations
(faults included); the edge-validated stream; the hybrid stream (edge-validated, with
the samples the central triage classed as sensor faults replaced by the spatial
estimate); and the noise-free truth (the upper bound, which still carries the error
of a single demand multiplier). The state-estimation error is the RMSE of simulated
against true pressure at the monitored pressure nodes and at unmonitored junctions.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from flowmine.central import triage
from flowmine.edge.core import quality
from flowmine.experiments.context import GRID, SeedContext, edge_flags, routine_mask
from flowmine.experiments.detection import spatial_long
from flowmine.sta.provision import thing_body
from flowmine.testbed import network
from flowmine.twin.bridge import sensors_from_things
from flowmine.twin.runner import run_twin


def unmonitored(ctx: SeedContext, n: int) -> list[str]:
    wn = network.load_network(ctx.cfg.network.inp)
    used = {s.element for s in ctx.tb.sensors}
    cands = sorted(j for j in wn.junction_name_list if j not in used)
    rng = np.random.default_rng([ctx.seed, 61])
    return sorted(rng.choice(cands, size=min(n, len(cands)), replace=False).tolist())


def hybrid_frame(ctx: SeedContext) -> pd.DataFrame:
    period = "test"
    frame = ctx.edge["edge_hw"].frame.copy()
    _, g, flag, est = spatial_long(ctx, frame, "val", period)
    ef = edge_flags(ctx.edge["edge_hw"])
    temporal = np.zeros_like(flag)
    sub = ef[ef.sensor_id.isin(g.streams) & (ef["shift"] | ef.reject) & ef.time_s.isin(g.times)]
    col = {s: j for j, s in enumerate(g.streams)}
    temporal[
        ((sub.time_s.to_numpy() - g.times[0]) // GRID).astype(int), [col[s] for s in sub.sensor_id]
    ] = True
    tri = triage.triage(temporal, flag, ctx.cfg.central.triage_window, routine=routine_mask(ctx, g))
    rows, cols = np.nonzero(tri.fault)
    fix = pd.DataFrame(
        {"stream": [g.streams[c] for c in cols], "time_s": g.times[rows], "new": est[rows, cols]}
    )
    m = frame.merge(fix, on=["stream", "time_s"], how="left")
    hit = m.new.notna()
    m.loc[hit, "value"] = m.loc[hit, "new"]
    m.loc[hit, "quality"] = (
        m.loc[hit, "quality"].astype(int) & ~quality.FLAG_MASK
    ) | quality.RECONSTRUCTED
    return m.drop(columns="new")


def hybrid_cached(ctx: SeedContext) -> pd.DataFrame:
    """The hybrid frame, computed once per seed (E6, E9 and the model validator use it)."""
    if "hybrid_frame" not in ctx.cache:
        ctx.cache["hybrid_frame"] = hybrid_frame(ctx)
    out = ctx.cache["hybrid_frame"]
    assert isinstance(out, pd.DataFrame)
    return out


def e6(ctx: SeedContext) -> pd.DataFrame:
    tb = ctx.tb
    t0, t1 = ctx.periods["test"]
    things = [
        thing_body(st, [s for s in tb.sensors if s.station == st.station_id], "")
        for st in tb.stations
    ]
    sensors = sensors_from_things(things)
    virt = unmonitored(ctx, ctx.cfg.experiments.unmonitored_nodes)
    wn = network.load_network(ctx.cfg.network.inp)
    inputs = {
        "raw": ctx.raw("test"),
        "edge_validated": ctx.edge["edge_hw"].frame,
        "hybrid_validated": hybrid_cached(ctx),
        "clean": tb.truth.rename(columns={"sensor_id": "stream"}).assign(quality=0),
    }
    truth_nodes = tb.nodes
    monitored = [s for s in tb.sensors if s.kind == "pressure"]
    rows = []
    for name, frame in inputs.items():
        run = run_twin(ctx.cfg, wn, sensors, frame, t0, t1, virt)
        idx = run.simulated.index
        err_m = np.concatenate(
            [
                (
                    run.simulated[f"SIM-{s.sensor_id}"] - truth_nodes[s.element].reindex(idx)
                ).to_numpy()
                for s in monitored
            ]
        )
        err_u = np.concatenate(
            [(run.simulated[f"VIRT-{n}"] - truth_nodes[n].reindex(idx)).to_numpy() for n in virt]
        )
        rows.append(
            {
                "seed": ctx.seed,
                "input": name,
                "rmse_monitored_m": float(np.sqrt(np.nanmean(err_m**2))),
                "rmse_unmonitored_m": float(np.sqrt(np.nanmean(err_u**2))),
                "p95_abs_error_m": float(
                    np.nanpercentile(np.abs(np.concatenate([err_m, err_u])), 95)
                ),
                "degraded_steps": float(run.degraded.mean()),
                "resyncs": run.resyncs,
            }
        )
    return pd.DataFrame(rows)
