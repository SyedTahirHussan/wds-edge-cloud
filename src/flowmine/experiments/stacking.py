"""E11: calibrated stacking with a false-alarm budget (P3.1).

The stacked model (hybrid/stacking.py) is fitted on week-7 labelled samples and its
threshold is chosen there for each budget in ``BUDGETS``; week 8 is scored with E1's
metrics, next to hybrid v0.1, which stays as it was. Outputs:

* ``e11_stacked``: E1 metrics (overall and by fault type) for hybrid v0.1 and the
  stacked model at each budget;
* ``e11_calib``: per budget, the threshold, the week-7 false-alarm rate (in-sample) and
  the realised week-8 rate;
* ``e11_coef``: standardised coefficients;
* ``e11_pareto``: (false-alarm rate, recall) on week 8 for each method, sweeping its
  continuous score (rule rejections, cross-checks and missing samples stay flagged
  at every threshold; chlorine samples keep the method's lab-CUSUM decision), plus
  each method's own operating point;
* ``e11_triage``: E7 recomputed for the stacked model (1 % budget).
"""

from __future__ import annotations

import networkx as nx
import numpy as np
import pandas as pd
import wntr

from flowmine.central import maintenance
from flowmine.edge import v2
from flowmine.edge.core import quality
from flowmine.experiments import detection
from flowmine.experiments.context import SeedContext
from flowmine.hybrid import stacking
from flowmine.hybrid.fusion import Fusion
from flowmine.hybrid.fusion import features as fusion_features
from flowmine.testbed import network
from flowmine.testbed.faults import GAPS

BUDGETS = (0.005, 0.01, 0.02)
HEADLINE = 0.01
TARGETS = tuple(float(v) for v in np.geomspace(1e-4, 0.3, 25))
FIXED = (
    quality.OUT_OF_RANGE | quality.RATE | quality.FLATLINE | quality.CROSSCHECK | quality.MISSING
)
PARETO_METHODS = ("edge_hw", "edge_v2", "central", "central_model", "hybrid", "stacked")


def col(budget: float) -> str:
    return f"stacked_{round(budget * 1000):03d}"


def _clip(x: pd.Series) -> np.ndarray:
    return np.clip(np.nan_to_num(x.to_numpy(dtype=float), nan=0.0), 0.0, stacking.CLIP)


def _cusum(ctx: SeedContext) -> pd.DataFrame:
    """CUSUM statistic over h per accepted sample, recomputed from the station's trace."""
    if "stack_cusum" in ctx.cache:
        out = ctx.cache["stack_cusum"]
        assert isinstance(out, pd.DataFrame)
        return out
    assert ctx.v2 is not None
    tr = ctx.edge["edge_v2"].trace
    assert tr is not None
    rows = []
    acc = tr[tr.f == 0].sort_values(["sensor_id", "time_s"], kind="stable")
    for sid, g in acc.groupby("sensor_id", sort=True):
        cs = ctx.v2.bundles[str(sid)].meta.get("cusum")
        if not cs:
            continue
        k, h = float(cs[0]), float(cs[1])  # type: ignore[index]
        stat = v2.cusum_stat(g.z.to_numpy(), k)
        rows.append(
            pd.DataFrame({"sensor_id": sid, "time_s": g.time_s.to_numpy(), "cusum_ratio": stat / h})
        )
    out = pd.concat(rows, ignore_index=True).drop_duplicates(["sensor_id", "time_s"], keep="last")
    ctx.cache["stack_cusum"] = out
    return out


def edge_scores(ctx: SeedContext, keys: pd.DataFrame, run: str = "edge_v2") -> pd.DataFrame:
    """Forecast residual score, |cross-sensor residual| and CUSUM ratio per key row."""
    er = ctx.edge[run]
    rec = er.records[["sensor_id", "time_s", "score"]].drop_duplicates(
        ["sensor_id", "time_s"], keep="last"
    )
    assert er.xtrace is not None
    xt = (
        er.xtrace.assign(cross_abs_z=er.xtrace.xz.abs())
        .groupby(["sensor_id", "time_s"], as_index=False)
        .cross_abs_z.max()
    )
    m = keys[["sensor_id", "time_s"]].merge(rec, on=["sensor_id", "time_s"], how="left")
    m = m.merge(xt, on=["sensor_id", "time_s"], how="left")
    return m.merge(_cusum(ctx), on=["sensor_id", "time_s"], how="left")


def _affected(ctx: SeedContext) -> dict[str, list[str]]:
    if "stack_affected" not in ctx.cache:
        wn: wntr.network.WaterNetworkModel = network.load_network(ctx.cfg.network.inp)
        g: nx.Graph = network.graph(wn)
        ops = ctx.tb.maintenance[ctx.tb.maintenance.type.isin(maintenance.OPERATIONS)]
        ctx.cache["stack_affected"] = {
            str(a): maintenance.affected_sensors(wn, g, str(a), ctx.tb.sensors)
            for a in ops.asset.unique()
        }
    out = ctx.cache["stack_affected"]
    assert isinstance(out, dict)
    return out


def maintenance_recent(ctx: SeedContext, keys: pd.DataFrame) -> np.ndarray:
    """True when an operation affecting the sensor was logged in the last suppress_h hours."""
    aff = _affected(ctx)
    ops = ctx.tb.maintenance[ctx.tb.maintenance.type.isin(maintenance.OPERATIONS)]
    span = ctx.cfg.central.suppress_h * 3600
    sid = keys.sensor_id.to_numpy()
    t = keys.time_s.to_numpy()
    out = np.zeros(len(keys), dtype=bool)
    for _, r in ops.iterrows():
        out |= np.isin(sid, aff.get(str(r.asset), [])) & (t >= r.time_s) & (t < r.time_s + span)
    return out


def feature_frame(ctx: SeedContext, f: pd.DataFrame) -> pd.DataFrame:
    """The stacking features for each row of a method_flags frame."""
    e = edge_scores(ctx, f)
    is_cl = f.sensor_id.isin(ctx.chlorine_ids).to_numpy()
    kind = f.sensor_id.map({s.sensor_id: s.kind for s in ctx.tb.sensors}).to_numpy()
    out = {
        "edge_reject": f.edge_v2.to_numpy(dtype=float),
        "edge_shift": f.edge_v2_shift.to_numpy(dtype=float),
        "edge_score": _clip(e.score),
        "edge_score_missing": e.score.isna().to_numpy(dtype=float),
        "cross_abs_z": _clip(e.cross_abs_z),
        "cross_missing": e.cross_abs_z.isna().to_numpy(dtype=float),
        "cusum_ratio": _clip(e.cusum_ratio),
        "cusum_missing": e.cusum_ratio.isna().to_numpy(dtype=float),
        "spatial_ratio": np.where(is_cl, 0.0, _clip(f.hybrid_ratio)),
        "spatial_missing": is_cl.astype(float),
        "model_ratio": _clip(f.model_ratio),
        "model_missing": f.model_ratio.isna().to_numpy(dtype=float),
        "lab_drift": f.lab_drift.to_numpy(dtype=float),
        **{f"kind_{k}": (kind == k).astype(float) for k in stacking.KINDS},
        "maintenance_recent": maintenance_recent(ctx, f).astype(float),
    }
    return pd.DataFrame(out)[list(stacking.FEATURES)]


def clean_of(f: pd.DataFrame) -> np.ndarray:
    return ((f.fault_type == "") & (f.event_id < 0)).to_numpy()


def fit(ctx: SeedContext) -> stacking.Stacked:
    """Fit on week 7 (gaps and missing samples left out); cached per seed."""
    if "stacked" in ctx.cache:
        out = ctx.cache["stacked"]
        assert isinstance(out, stacking.Stacked)
        return out
    fc = ctx.cache.get("flags_calib")
    if not isinstance(fc, pd.DataFrame):
        fc = detection.method_flags(ctx, "calib", None)
    x = stacking.matrix(feature_frame(ctx, fc))
    keep = (~fc.fault_type.isin(GAPS) & ~fc.missing).to_numpy()
    y = (fc.fault_type != "").to_numpy()
    model = stacking.Stacked.fit(x[keep], y[keep], clean_of(fc)[keep], BUDGETS, ctx.seed)
    p = model.proba(x)
    ctx.cache["stacked"] = model
    ctx.cache["stacked_calib"] = (fc, p, keep)
    return model


def add_flags(ctx: SeedContext, f: pd.DataFrame, model: stacking.Stacked) -> pd.DataFrame:
    """Copy of f with the stacked probability and one flag column per budget."""
    out = f.copy()
    p = model.proba(stacking.matrix(feature_frame(ctx, f)))
    out["stacked_p"] = p
    for b in BUDGETS:
        out[col(b)] = (p > model.thresholds[b]) | out.missing.to_numpy()
    return out


def _sweep(
    method: str, score: np.ndarray, fixed: np.ndarray, clean: np.ndarray, fault: np.ndarray
) -> list[dict[str, object]]:
    cs = score[clean & ~fixed]
    rows: list[dict[str, object]] = []
    for a in TARGETS:
        thr = float(np.quantile(cs, 1.0 - a)) if len(cs) else np.inf
        flag = fixed | (score > thr)
        rows.append(
            {
                "method": method,
                "point": "sweep",
                "target": a,
                "false_alarm_rate": float(flag[clean].mean()),
                "recall": float(flag[fault].mean()),
            }
        )
    return rows


def _record_scores(ctx: SeedContext, f: pd.DataFrame, run: str) -> tuple[np.ndarray, np.ndarray]:
    r = (
        ctx.edge[run]
        .records[["sensor_id", "time_s", "q", "score"]]
        .drop_duplicates(["sensor_id", "time_s"], keep="last")
    )
    m = f[["sensor_id", "time_s"]].merge(r, on=["sensor_id", "time_s"], how="left")
    q = m.q.fillna(quality.MISSING).astype(int).to_numpy()
    score = m.score.fillna(0.0).to_numpy(dtype=float)
    fixed = (q & FIXED) != 0
    # v0.2: a drift (CUSUM) flag sets MODEL_RESIDUAL on a sample whose residual passed.
    fixed |= (
        ((q & quality.MODEL_RESIDUAL) != 0) & ((q & quality.RECONSTRUCTED) == 0) & (score <= 1.0)
    )
    return score, fixed | f.missing.to_numpy()


def pareto(ctx: SeedContext, f: pd.DataFrame, fusion: Fusion) -> pd.DataFrame:
    clean = clean_of(f)
    fault = ((f.fault_type != "") & ~f.fault_type.isin(GAPS)).to_numpy()
    miss = f.missing.to_numpy()
    is_cl = f.sensor_id.isin(ctx.chlorine_ids).to_numpy()
    xh = fusion_features(
        f.edge_hw.to_numpy(), f.edge_hw_shift.to_numpy(), f.hybrid_ratio.to_numpy()
    )
    p_h = np.where(is_cl, -1.0, fusion.model.predict_proba(xh)[:, 1])
    scores = {
        "edge_hw": _record_scores(ctx, f, "edge_hw"),
        "edge_v2": _record_scores(ctx, f, "edge_v2"),
        "central": (
            np.where(is_cl, -1.0, f.central_ratio.to_numpy(dtype=float)),
            miss | (is_cl & f.central.to_numpy()),
        ),
        "central_model": (np.nan_to_num(f.model_ratio.to_numpy(dtype=float), nan=0.0), miss),
        "hybrid": (p_h, miss | (is_cl & f.hybrid.to_numpy())),
        "stacked": (f.stacked_p.to_numpy(dtype=float), miss),
    }
    rows: list[dict[str, object]] = []
    for m in PARETO_METHODS:
        s, fx = scores[m]
        rows += _sweep(m, s, fx, clean, fault)
        flag = f[col(HEADLINE) if m == "stacked" else m].to_numpy(dtype=bool)
        rows.append(
            {
                "method": m,
                "point": "operating",
                "target": np.nan,
                "false_alarm_rate": float(flag[clean].mean()),
                "recall": float(flag[fault].mean()),
            }
        )
    return pd.DataFrame(rows).assign(seed=ctx.seed)


def e11(ctx: SeedContext, flags: pd.DataFrame) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """E11 tables, and the week-8 flags frame with the stacked columns added."""
    model = fit(ctx)
    fusion = ctx.cache["fusion"]
    assert isinstance(fusion, Fusion)
    f = add_flags(ctx, flags, model)
    methods = ("hybrid", *(col(b) for b in BUDGETS))
    scored = detection.score(ctx, f, methods).assign(seed=ctx.seed)
    cal = ctx.cache["stacked_calib"]
    assert isinstance(cal, tuple)
    fc: pd.DataFrame = cal[0]
    pc: np.ndarray = cal[1]
    keep: np.ndarray = cal[2]
    cc = clean_of(fc) & keep
    ct = clean_of(f)
    fault_c = ((fc.fault_type != "") & ~fc.fault_type.isin(GAPS)).to_numpy() & keep
    fault_t = ((f.fault_type != "") & ~f.fault_type.isin(GAPS)).to_numpy()
    calib = pd.DataFrame(
        [
            {
                "seed": ctx.seed,
                "budget": b,
                "threshold": model.thresholds[b],
                "calib_false_alarm_rate": float((pc[cc] > model.thresholds[b]).mean()),
                "test_false_alarm_rate": float(f[col(b)].to_numpy()[ct].mean()),
                "calib_recall": float((pc[fault_c] > model.thresholds[b]).mean()),
                "test_recall": float(f[col(b)].to_numpy()[fault_t].mean()),
                "n_train": int(keep.sum()),
                "n_train_faults": int(((fc.fault_type != "").to_numpy() & keep).sum()),
            }
            for b in BUDGETS
        ]
    )
    head = f[["sensor_id", "time_s"]].assign(flag=f[col(HEADLINE)].to_numpy())
    tri = detection.e7(ctx, stacked=head).assign(budget=HEADLINE)
    return {
        "e11_stacked": scored,
        "e11_calib": calib,
        "e11_coef": model.coefficients().assign(seed=ctx.seed),
        "e11_pareto": pareto(ctx, f, fusion),
        "e11_triage": tri,
    }, f
