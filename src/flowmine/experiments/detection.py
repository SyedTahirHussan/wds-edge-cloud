"""E1 (fault detection) and E7 (process events versus sensor faults).

Scores use the test week only. Sample-based: a labelled sample is a true positive
when flagged. Per fault type, precision is TP_type / (TP_type + FP) with FP the
flagged samples that carry neither a fault nor a process-event label (the precision
one would see if only that fault type existed); alarms on samples changed by a
process event are reported separately as ``process_event_alarm_rate`` (E7 looks at
them in detail); overall precision pools all non-gap faults. Gaps are
reported but kept out of "overall" because every method sees a missing sample.
Event-based: a fault event is detected when any of its samples is flagged, the delay
runs from its first sample to the first flagged one; an alarm episode (a run of
flagged samples of one sensor) is correct when it overlaps a fault.
"""

from __future__ import annotations

import networkx as nx
import numpy as np
import pandas as pd
import wntr

from flowmine.central import homogenise, maintenance, triage
from flowmine.experiments.context import (
    GRID,
    SeedContext,
    clean_mask,
    drift_flags,
    edge_flags,
    routine_mask,
)
from flowmine.hybrid.fusion import Fusion, features
from flowmine.testbed.faults import FAULT_TYPES, GAPS

METHODS = ("rules", "edge_hw", "edge_mlp", "edge_v2", "central", "hybrid")


def spatial_long(
    ctx: SeedContext, frame: pd.DataFrame, which: str, period: str
) -> tuple[pd.DataFrame, homogenise.Grid, np.ndarray, np.ndarray]:
    """Spatial ratio |z|/threshold and flag at each sensor's own sample times."""
    model = ctx.spatial_raw if which == "raw" else ctx.spatial_val
    g = ctx.grid(frame, period)
    r = model.apply(g.values, g.suspect)
    ratio = np.abs(np.nan_to_num(r.z)) / model.z_thr
    rows = []
    for j, s in enumerate(g.streams):
        per = ctx.tb.sensor(s).period_s
        sel = (g.times % per) == 0
        rows.append(
            pd.DataFrame(
                {
                    "sensor_id": s,
                    "time_s": g.times[sel],
                    "ratio": ratio[sel, j],
                    "estimate": r.estimate[sel, j],
                }
            )
        )
    return pd.concat(rows, ignore_index=True), g, ratio > 1.0, r.estimate


def labels(ctx: SeedContext, period: str) -> pd.DataFrame:
    t0, t1 = ctx.periods[period]
    lab = ctx.tb.labels
    out = lab[(lab.time_s >= t0) & (lab.time_s < t1)].reset_index(drop=True)
    obs = ctx.tb.observed
    o = obs[(obs.time_s >= t0) & (obs.time_s < t1)]
    out = out.merge(o.rename(columns={"value": "observed"}), on=["sensor_id", "time_s"])
    out["missing"] = out.observed.isna()
    return out


def _join(base: pd.DataFrame, extra: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    m = base[["sensor_id", "time_s"]].merge(extra, on=["sensor_id", "time_s"], how="left")
    for c in cols:
        m[c] = m[c].fillna(False if m[c].dtype == object or c != "ratio" else 0.0)
    return m


def method_flags(ctx: SeedContext, period: str, fusion: Fusion | None) -> pd.DataFrame:
    lab = labels(ctx, period)
    out = lab.copy()
    for name in ("rules", "edge_hw", "edge_mlp", "edge_v2"):
        f = _join(lab, edge_flags(ctx.edge[name]), ["reject", "shift"])
        out[name] = f.reject.astype(bool).to_numpy() | lab.missing.to_numpy()
        out[f"{name}_shift"] = f["shift"].astype(bool).to_numpy()
    t1 = ctx.periods[period][1]
    sp_raw, _, _, _ = spatial_long(ctx, ctx.raw(period), "raw", period)
    sp_val, _, _, _ = spatial_long(ctx, ctx.edge["edge_hw"].frame, "val", period)
    raw_r = _join(lab, sp_raw, ["ratio"]).ratio.astype(float).to_numpy()
    val_r = _join(lab, sp_val, ["ratio"]).ratio.astype(float).to_numpy()
    out["central_ratio"] = raw_r
    out["hybrid_ratio"] = val_r
    raw_frame = homogenise.raw_frame(ctx.tb.observed, ctx.periods["train"][0], t1)
    key = list(zip(lab.sensor_id, lab.time_s, strict=True))
    d_raw = set(map(tuple, drift_flags(ctx, raw_frame, period).to_numpy()))
    d_val = set(map(tuple, drift_flags(ctx, ctx.edge["edge_hw"].frame, period).to_numpy()))
    in_raw = np.array([(s, int(t)) in d_raw for s, t in key])
    in_val = np.array([(s, int(t)) in d_val for s, t in key])
    is_cl = lab.sensor_id.isin(ctx.chlorine_ids).to_numpy()
    out["central"] = np.where(is_cl, in_raw, raw_r > 1.0) | lab.missing.to_numpy()
    x = features(out.edge_hw.to_numpy(), out.edge_hw_shift.to_numpy(), val_r)
    fused = fusion.predict(x) if fusion is not None else np.zeros(len(out), dtype=bool)
    out["hybrid"] = np.where(is_cl, out.edge_hw.to_numpy() | in_val, fused) | lab.missing.to_numpy()
    out["x_reject"], out["x_shift"] = x[:, 0], x[:, 1]
    return out


def fit_fusion(ctx: SeedContext) -> Fusion:
    f = method_flags(ctx, "calib", None)
    hyd = ~f.sensor_id.isin(ctx.chlorine_ids) & ~f.fault_type.isin(GAPS)
    x = features(f.edge_hw.to_numpy(), f.edge_hw_shift.to_numpy(), f.hybrid_ratio.to_numpy())[hyd]
    y = (f.fault_type != "").to_numpy()[hyd]
    return Fusion.fit(x, y, ctx.seed)


def _episodes(
    flags: pd.DataFrame, method: str, period_of: dict[str, int]
) -> list[tuple[str, int, int]]:
    eps = []
    for sid, g in flags[flags[method]].groupby("sensor_id"):
        t = np.sort(g.time_s.to_numpy())
        per = period_of[str(sid)]
        start = prev = t[0]
        for x in t[1:]:
            if x - prev > 2 * per:
                eps.append((str(sid), int(start), int(prev)))
                start = x
            prev = x
        eps.append((str(sid), int(start), int(prev)))
    return eps


def score(
    ctx: SeedContext, flags: pd.DataFrame, methods: tuple[str, ...] = METHODS
) -> pd.DataFrame:
    """Rows: method x fault type (plus 'overall') with sample and event metrics."""
    rows = []
    period_of = {s.sensor_id: s.period_s for s in ctx.tb.sensors}
    faults = ctx.tb.faults[ctx.tb.faults.period == "test"]
    for m in methods:
        clean = (flags.fault_type == "") & (flags.event_id < 0)
        in_event = (flags.fault_type == "") & (flags.event_id >= 0)
        fp = int((flags[m] & clean).sum())
        clean_n = int(clean.sum())
        event_rate = float(flags[m][in_event].mean()) if in_event.any() else np.nan
        nongap = flags[(flags.fault_type != "") & ~flags.fault_type.isin(GAPS)]
        for ft in (*FAULT_TYPES, "overall"):
            sel = nongap if ft == "overall" else flags[flags.fault_type == ft]
            tp = int(sel[m].sum())
            n = len(sel)
            prec = tp / (tp + fp) if tp + fp else np.nan
            rec = tp / n if n else np.nan
            f1 = 2 * prec * rec / (prec + rec) if n and tp else 0.0
            fe = faults if ft == "overall" else faults[faults.fault_type == ft]
            if ft == "overall":
                fe = fe[~fe.fault_type.isin(GAPS)]
            det, delays = 0, []
            for _, e in fe.iterrows():
                g = flags[(flags.fault_id == e.fault_id) & flags[m]]
                if len(g):
                    det += 1
                    delays.append((g.time_s.min() - e.start_idx * period_of[e.sensor_id]) / 60.0)
            rows.append(
                {
                    "method": m,
                    "fault_type": ft,
                    "precision": prec,
                    "recall": rec,
                    "f1": f1,
                    "event_recall": det / len(fe) if len(fe) else np.nan,
                    "delay_min": float(np.mean(delays)) if delays else np.nan,
                    "false_alarm_rate": fp / clean_n if clean_n else np.nan,
                    "process_event_alarm_rate": event_rate,
                }
            )
        eps = _episodes(flags[~flags.fault_type.isin(GAPS) & ~flags.missing], m, period_of)
        good = 0
        for sid, a, b in eps:
            g = flags[(flags.sensor_id == sid) & (flags.time_s >= a) & (flags.time_s <= b)]
            good += int(((g.fault_type != "") & ~g.fault_type.isin(GAPS)).any())
        rows[-1]["event_precision"] = good / len(eps) if eps else np.nan
    return pd.DataFrame(rows)


def e1(ctx: SeedContext) -> tuple[pd.DataFrame, pd.DataFrame]:
    fusion = fit_fusion(ctx)
    flags = method_flags(ctx, "test", fusion)
    return score(ctx, flags).assign(seed=ctx.seed), flags


def maintenance_ablation(
    ctx: SeedContext, wn: wntr.network.WaterNetworkModel, g_net: nx.Graph
) -> pd.DataFrame:
    """Spatial false alarms on the test week with and without the maintenance log.

    Negatives are samples with no fault label, including those changed by a valve
    operation or pump maintenance (a real change, so an alarm there is false).
    """
    _, g, flag, _ = spatial_long(ctx, ctx.edge["edge_hw"].frame, "val", "test")
    negatives = (clean_mask(ctx.tb, g) | _event_ops(ctx, g)) & np.isfinite(g.values)
    faulty = _fault_mask(ctx, g) & np.isfinite(g.values)
    log = ctx.tb.maintenance
    ops = log[log.type.isin(maintenance.OPERATIONS)]
    aff = {
        str(a): maintenance.affected_sensors(wn, g_net, str(a), ctx.tb.sensors)
        for a in ops.asset.unique()
    }
    sup = maintenance.suppression_mask(g.times, g.streams, log, aff, ctx.cfg.central.suppress_h)
    return pd.DataFrame(
        [
            {
                "seed": ctx.seed,
                "false_alarms_without_log": int((flag & negatives).sum()),
                "false_alarms_with_log": int((flag & negatives & ~sup).sum()),
                "fault_alarms_suppressed": int((flag & faulty & sup).sum()),
                "operations": len(ops[(ops.time_s >= g.times[0]) & (ops.time_s < g.times[-1])]),
            }
        ]
    )


def _grid_mask(g: homogenise.Grid, sub: pd.DataFrame) -> np.ndarray:
    mask = np.zeros_like(g.suspect)
    col = {s: j for j, s in enumerate(g.streams)}
    mask[
        ((sub.time_s.to_numpy() - g.times[0]) // GRID).astype(int), [col[s] for s in sub.sensor_id]
    ] = True
    return mask


def _fault_mask(ctx: SeedContext, g: homogenise.Grid) -> np.ndarray:
    """Samples carrying a non-gap sensor-fault label (process events are not faults)."""
    lab = ctx.tb.labels
    sub = lab[
        lab.sensor_id.isin(g.streams)
        & lab.time_s.isin(g.times)
        & (lab.fault_type != "")
        & ~lab.fault_type.isin(GAPS)
    ]
    return _grid_mask(g, sub)


def _event_ops(ctx: SeedContext, g: homogenise.Grid) -> np.ndarray:
    """Samples labelled with an operation (valve/pump) event: real changes, not faults."""
    ev = ctx.tb.events
    ops = set(ev[ev.event_type.isin(["valve_operation", "pump_maintenance"])].event_id)
    lab = ctx.tb.labels
    sub = lab[
        lab.sensor_id.isin(g.streams)
        & lab.time_s.isin(g.times)
        & lab.event_id.isin(ops)
        & (lab.fault_type == "")
    ]
    return _grid_mask(g, sub)


def e7(ctx: SeedContext) -> pd.DataFrame:
    """Process events reconstructed away / sensor faults raised as events, per architecture."""
    period = "test"
    w = ctx.cfg.central.triage_window
    _, _, flag_raw, _ = spatial_long(ctx, ctx.raw(period), "raw", period)
    _, g_val, flag_val, _ = spatial_long(ctx, ctx.edge["edge_hw"].frame, "val", period)
    ef = edge_flags(ctx.edge["edge_hw"])
    t0 = g_val.times[0]

    def on_grid(col: str) -> np.ndarray:
        m = np.zeros_like(g_val.suspect)
        sub = ef[ef.sensor_id.isin(g_val.streams) & ef[col] & ef.time_s.isin(g_val.times)]
        idx = {s: j for j, s in enumerate(g_val.streams)}
        m[((sub.time_s.to_numpy() - t0) // GRID).astype(int), [idx[s] for s in sub.sensor_id]] = (
            True
        )
        return m

    temporal = on_grid("shift") | on_grid("reject")
    routine = routine_mask(ctx, g_val)
    hyb = triage.triage(temporal, flag_val, w, routine=routine)
    cen = triage.triage(np.zeros_like(flag_raw), flag_raw, w)
    central_event = np.zeros_like(flag_raw)
    for w0 in range(0, len(flag_raw), w):
        sl = slice(w0, w0 + w)
        any_s = flag_raw[sl].any(axis=0)
        if any_s.sum() >= 2 and not routine[sl].any():
            central_event[sl] = any_s[None, :]
    arch = {
        "edge_only": (on_grid("reconstructed"), on_grid("shift")),
        "central_only": (cen.fault, central_event),
        "hybrid": (hyb.fault, hyb.event_sensors),
    }
    lab = ctx.tb.labels
    idx = {s: j for j, s in enumerate(g_val.streams)}
    events = ctx.tb.events[
        (ctx.tb.events.period == period) & ctx.tb.events.event_type.isin(["leak", "surge"])
    ]
    faults = ctx.tb.faults[
        (ctx.tb.faults.period == period)
        & ~ctx.tb.faults.fault_type.isin(GAPS)
        & ctx.tb.faults.sensor_id.isin(g_val.streams)
    ]
    rows = []
    for name, (recon, raised) in arch.items():
        fracs, away, detected = [], 0, 0
        for _, e in events.iterrows():
            sub = lab[
                (lab.event_id == e.event_id)
                & lab.sensor_id.isin(g_val.streams)
                & (lab.fault_type == "")
                & lab.time_s.isin(g_val.times)
            ]
            if sub.empty:
                continue
            r = ((sub.time_s.to_numpy() - t0) // GRID).astype(int)
            c = np.array([idx[s] for s in sub.sensor_id])
            frac = float(recon[r, c].mean())
            fracs.append(frac)
            away += int(frac >= 0.5)
            detected += int(raised[r, c].any())
        n_raised = 0
        for _, f in faults.iterrows():
            per = ctx.tb.sensor(f.sensor_id).period_s
            a = (f.start_idx * per - t0) // GRID
            b = ((f.start_idx + f.n) * per - t0) // GRID
            n_raised += int(raised[a:b, idx[f.sensor_id]].any())
        rows.append(
            {
                "seed": ctx.seed,
                "architecture": name,
                "event_samples_reconstructed": float(np.mean(fracs)) if fracs else np.nan,
                "events_reconstructed_away": away / len(fracs) if fracs else np.nan,
                "faults_raised_as_events": n_raised / len(faults) if len(faults) else np.nan,
                "process_events_raised": detected / len(fracs) if fracs else np.nan,
                "n_events": len(fracs),
                "n_faults": len(faults),
            }
        )
    return pd.DataFrame(rows)
