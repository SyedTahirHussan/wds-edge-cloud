"""E8: the v0.2 station (conformal thresholds, CUSUM, cross-sensor model, intervals).

* E8a, conformal coverage: for alpha in ``CONF_ALPHAS`` and with 1 or 4 time-of-day
  buckets, the station runs with only the conformal residual thresholds installed.
  On clean samples (no fault, no process-event label) that had a forecast, were not
  rejected by a rule test and were not released as level shifts, the empirical
  share rejected by the residual test is compared with the nominal alpha, on the
  calibration week (in-sample) and on the test week.
* E8b, reconstruction intervals: artificial gaps on clean stretches, as in E2. The
  errors of the Holt-Winters and spatial reconstructions on calibration-week gaps,
  divided by the sensor's training residual scale and grouped by horizon (time
  since the gap started), give split conformal half-widths at level
  ``INTERVAL_ALPHA``. On test-week gaps: coverage and mean width by gap length. Two
  selectors are compared: v0.1's fixed crossover (by gap length, from calibration
  gaps) and the narrower calibrated interval at each horizon.
* E8c, ablation of the station: v0.1, conformal only, conformal + CUSUM,
  conformal + cross-sensor model, and all three (``edge_v2`` in E1), with the E1
  metrics.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from flowmine.edge import v2
from flowmine.edge.core import conformal, quality
from flowmine.experiments import detection
from flowmine.experiments.context import SeedContext, edge_flags, run_edge
from flowmine.experiments.reconstruction import _fed_values, _gaps, crossover

CONF_ALPHAS = (0.01, 0.005, 0.001)
INTERVAL_ALPHA = 0.1
HORIZONS_S = (900, 3600, 10800, 21600, 43200, 86400)
RULES = quality.OUT_OF_RANGE | quality.RATE | quality.FLATLINE | quality.MISSING
VARIANTS = ("edge_hw", "v2_conformal", "v2_conformal_cusum", "v2_conformal_cross", "edge_v2")


def _exceedance(ctx: SeedContext, rec: pd.DataFrame, period: str) -> tuple[int, float, float]:
    t0, t1 = ctx.periods[period]
    r = rec[(rec.time_s >= t0) & (rec.time_s < t1)].merge(ctx.tb.labels, on=["sensor_id", "time_s"])
    clean = (r.fault_type == "") & (r.event_id < 0)
    base = r[clean & (r.score > 0) & ((r.q & RULES) == 0) & ((r.q & quality.SHIFT) == 0)]
    exceed = float(((base.q & quality.MODEL_RESIDUAL) != 0).mean()) if len(base) else np.nan
    rej = (r.q & (quality.REJECTING | quality.CROSSCHECK | quality.MISSING)) != 0
    return len(base), exceed, float(rej[clean].mean())


def e8a(ctx: SeedContext) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    assert ctx.v2 is not None
    t0, t1 = ctx.periods["calib"][0], ctx.periods["test"][1]
    rows, runs = [], {}
    for buckets in (1, 4):
        for alpha in CONF_ALPHAS:
            tables = v2.conformal_tables(ctx.v2.conf_trace, alpha, buckets)
            b = v2.with_conformal(ctx.bundles, tables)
            run = run_edge(ctx.tb, b, "hw", "validated", t0, t1)
            if buckets == 1 and alpha == ctx.cfg.edge.conformal_alpha:
                runs["v2_conformal"] = run.records
            infinite = sum(1 for t in tables.values() for q in t if q >= conformal.INF)
            for period in ("calib", "test"):
                n, ex, far = _exceedance(ctx, run.records, period)
                rows.append(
                    {
                        "seed": ctx.seed,
                        "buckets": buckets,
                        "alpha": alpha,
                        "period": period,
                        "n_samples": n,
                        "empirical_rate": ex,
                        "edge_false_alarm_rate": far,
                        "infinite_thresholds": infinite,
                    }
                )
    return pd.DataFrame(rows), runs


def e8c(ctx: SeedContext, conformal_records: pd.DataFrame) -> pd.DataFrame:
    assert ctx.v2 is not None
    t0, t1 = ctx.periods["calib"][0], ctx.periods["test"][1]
    recs = {
        "edge_hw": ctx.edge["edge_hw"].records,
        "v2_conformal": conformal_records,
        "v2_conformal_cusum": run_edge(
            ctx.tb, v2.without(ctx.v2.bundles, ("cross",)), "hw", "validated", t0, t1
        ).records,
        "v2_conformal_cross": run_edge(
            ctx.tb, v2.without(ctx.v2.bundles, ("cusum",)), "hw", "validated", t0, t1
        ).records,
        "edge_v2": ctx.edge["edge_v2"].records,
    }
    lab = detection.labels(ctx, "test")
    flags = lab.copy()
    for name, r in recs.items():
        ef = edge_flags(_Run(r))  # type: ignore[arg-type]
        f = detection._join(lab, ef, ["reject", "shift"])
        flags[name] = f.reject.astype(bool).to_numpy() | lab.missing.to_numpy()
    return (
        detection.score(ctx, flags, VARIANTS)
        .rename(columns={"method": "variant"})
        .assign(seed=ctx.seed)
    )


class _Run:
    def __init__(self, records: pd.DataFrame) -> None:
        self.records = records


def _hw_estimate(ctx: SeedContext, sid: str, start: int, end: int) -> np.ndarray:
    m = ctx.bundles[sid].forecaster("hw")
    for t, v in _fed_values(ctx, sid, start):
        m.update(v, t)
    out = []
    for tt in range(start, end, ctx.tb.sensor(sid).period_s):
        out.append(m.forecast_at(tt))
        m.update(None, tt)
    return np.array(out)


def _gap_errors(ctx: SeedContext, period: str, n_per: int, seed_tag: int) -> pd.DataFrame:
    rng = np.random.default_rng([ctx.seed, seed_tag])
    truth = ctx.tb.truth.set_index(["sensor_id", "time_s"]).value
    g = ctx.grid(ctx.edge["edge_hw"].frame, period)
    rows = []
    for gap, (sid, start, end) in enumerate(_gaps(ctx, period, n_per, rng)):
        s = ctx.tb.sensor(sid)
        times = np.arange(start, end, s.period_s)
        tv = truth.loc[sid].reindex(times).to_numpy()
        j = g.col(sid)
        sus = g.suspect.copy()
        sel_rows = (g.times >= start) & (g.times < end)
        sus[sel_rows, j] = True
        sp = ctx.spatial_val.apply(g.values, sus).estimate[
            sel_rows & (g.times % s.period_s == 0), j
        ]
        scale = float(ctx.bundles[sid].meta["init_scale"])  # type: ignore[arg-type]
        est = {"holt_winters": _hw_estimate(ctx, sid, start, end), "spatial": sp}
        for method, v in est.items():
            rows.append(
                pd.DataFrame(
                    {
                        "gap": gap,
                        "sensor_id": sid,
                        "kind": s.kind,
                        "gap_min": int((end - start) // 60),
                        "method": method,
                        "horizon_s": times - start + s.period_s,
                        "err": v - tv,
                        "scale": scale,
                    }
                )
            )
    return pd.concat(rows, ignore_index=True)


def _bucket(h: np.ndarray) -> np.ndarray:
    return np.searchsorted(np.array(HORIZONS_S), h, side="left")


def interval_tables(cal: pd.DataFrame, alpha: float) -> dict[str, list[tuple[int, float]]]:
    """Per method: [(horizon_s, q)] with q the conformal quantile of |err| / scale."""
    out = {}
    b = _bucket(cal.horizon_s.to_numpy())
    score = np.abs(cal.err.to_numpy()) / cal.scale.to_numpy()
    for method in ("holt_winters", "spatial"):
        m = (cal.method == method).to_numpy()
        out[method] = [
            (h, float(conformal.split_quantile(score[m & (b == i)].tolist(), alpha)))
            for i, h in enumerate(HORIZONS_S)
        ]
    return out


def e8b(ctx: SeedContext) -> tuple[pd.DataFrame, pd.DataFrame]:
    n = ctx.cfg.experiments.gaps_per_length
    cal = _gap_errors(ctx, "calib", n, 81)
    test = _gap_errors(ctx, "test", n, 82)
    tables = interval_tables(cal, INTERVAL_ALPHA)
    # v0.1 selector: crossover gap length from calibration-gap MAE (normalised per kind).
    mae = (
        cal.assign(ae=cal.err.abs())
        .groupby(["gap", "kind", "gap_min", "method"])
        .ae.mean()
        .reset_index()
        .rename(columns={"ae": "mae"})
    )
    mae["mae"] = mae.mae / mae.groupby("kind").mae.transform("mean")
    cross = crossover(mae)
    test = test.copy()
    hw_q = dict(tables["holt_winters"])
    sp_q = dict(tables["spatial"])
    hb = np.array(HORIZONS_S)[np.minimum(_bucket(test.horizon_s.to_numpy()), len(HORIZONS_S) - 1)]
    q = np.where(
        test.method == "holt_winters", [hw_q[int(h)] for h in hb], [sp_q[int(h)] for h in hb]
    )
    test["half"] = q * test.scale.to_numpy()
    test["covered"] = np.abs(test.err) <= test.half
    hw = test[test.method == "holt_winters"].reset_index(drop=True)
    sp = test[test.method == "spatial"].reset_index(drop=True)
    pick_sp_width = np.array(
        [sp_q[int(h)] < hw_q[int(h)] for h in hb[test.method == "holt_winters"]]
    )
    pick_sp_cross = (hw.gap_min >= cross).to_numpy()
    sel = []
    for name, pick in (("hybrid_crossover", pick_sp_cross), ("hybrid_width", pick_sp_width)):
        h = hw.copy()
        for c in ("err", "half", "covered"):
            h[c] = np.where(pick, sp[c], hw[c])
        h["method"] = name
        sel.append(h)
    allm = pd.concat([test, *sel], ignore_index=True)
    allm["ae"] = allm.err.abs()
    allm["width"] = 2 * allm.half
    per_gap = (
        allm.groupby(["gap", "sensor_id", "kind", "gap_min", "method"])
        .agg(mae=("ae", "mean"), coverage=("covered", "mean"), width=("width", "mean"))
        .reset_index()
        .assign(seed=ctx.seed, crossover_min=cross)
    )
    tab = pd.DataFrame(
        [
            {"seed": ctx.seed, "method": m, "horizon_s": h, "q": qq}
            for m, rows in tables.items()
            for h, qq in rows
        ]
    )
    return per_gap, tab


def e8(ctx: SeedContext) -> dict[str, pd.DataFrame]:
    a, runs = e8a(ctx)
    b, tab = e8b(ctx)
    c = e8c(ctx, runs["v2_conformal"])
    assert ctx.v2 is not None
    xf = pd.DataFrame(
        [
            {
                "seed": ctx.seed,
                "sensor_id": x.sensor_id,
                "station": x.station,
                "chosen": x.chosen,
                "rmse_ridge_calib": x.rmse_ridge,
                "rmse_mlp_calib": x.rmse_mlp,
                "residual_sd": x.spec["s"],
                "threshold_q": dict(ctx.v2.bundles[x.sensor_id].meta["xmodel"])["q"],  # type: ignore[call-overload]
            }
            for x in ctx.v2.xfits
        ]
    )
    return {
        "e8a_conformal": a,
        "e8b_intervals": b,
        "e8b_tables": tab,
        "e8c_ablation": c,
        "e8_xmodels": xf,
        "e8_cusum": ctx.v2.cusum_choice.assign(seed=ctx.seed),
    }
