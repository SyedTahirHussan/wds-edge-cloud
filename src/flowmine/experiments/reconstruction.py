"""E2: reconstruction error versus gap length.

Artificial gaps are placed on clean stretches (no fault, no process event) of the
hydraulic sensors, cycling through pressure, flow and level. Every method sees the
same gaps. The temporal forecasters start from the edge state at the end of training
and are fed what the station accepted up to the gap start; the spatial estimate uses
the other sensors' edge-validated values. The hybrid rule uses the temporal estimate
for gaps shorter than a crossover length and the spatial one beyond; the crossover is
found by running the same comparison on the calibration week. Errors are against the
noise-free truth, in physical units.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from flowmine.edge.core import quality
from flowmine.experiments.context import SeedContext

TEMPORAL = ("last_value", "linear_offline", "seasonal_naive", "holt_winters", "mlp")
KINDS = ("pressure", "flow", "level")


def _gaps(
    ctx: SeedContext, period: str, n_per: int, rng: np.random.Generator
) -> list[tuple[str, int, int]]:
    t0, t1 = ctx.periods[period]
    lab = ctx.tb.labels
    sens = {k: [s for s in ctx.tb.sensors if s.kind == k] for k in KINDS}
    out = []
    for minutes in ctx.cfg.experiments.gap_lengths_min:
        for i in range(n_per):
            kind = KINDS[i % len(KINDS)]
            for _attempt in range(200):
                s = sens[kind][int(rng.integers(len(sens[kind])))]
                n = max(1, minutes * 60 // s.period_s)
                start = (
                    int(rng.integers(t0 // s.period_s + 2, t1 // s.period_s - n - 2)) * s.period_s
                )
                end = start + n * s.period_s
                win = lab[
                    (lab.sensor_id == s.sensor_id)
                    & (lab.time_s >= start - s.period_s)
                    & (lab.time_s <= end)
                ]
                if (win.fault_type == "").all() and (win.event_id < 0).all():
                    out.append((s.sensor_id, start, end))
                    break
    return out


def _fed_values(ctx: SeedContext, sid: str, until: int) -> list[tuple[int, float | None]]:
    """What the station's forecaster saw from the end of training up to ``until``."""
    r = ctx.edge["edge_hw"].records
    r = r[(r.sensor_id == sid) & (r.time_s < until)].sort_values("time_s")
    ok = (r.q & (quality.REJECTING | quality.MISSING)) == 0
    return [
        (int(t), float(v) if good else None) for t, v, good in zip(r.time_s, r.raw, ok, strict=True)
    ]


def temporal_estimates(ctx: SeedContext, sid: str, start: int, end: int) -> dict[str, np.ndarray]:
    s = ctx.tb.sensor(sid)
    per = s.period_s
    times = np.arange(start, end, per)
    obs = ctx.tb.observed
    o = obs[obs.sensor_id == sid].set_index("time_s").value
    before = o.loc[: start - per].dropna()
    after = o.loc[end:].dropna()
    last = float(before.iloc[-1])
    nxt_t, nxt_v = int(after.index[0]), float(after.iloc[0])
    lin = last + (nxt_v - last) * (times - before.index[-1]) / (nxt_t - before.index[-1])
    fed = _fed_values(ctx, sid, start)
    b = ctx.bundles[sid]
    out = {"last_value": np.full(len(times), last), "linear_offline": lin}
    for name, kind in (("seasonal_naive", "snaive"), ("holt_winters", "hw"), ("mlp", "mlp")):
        m = b.forecaster(kind)
        for t, v in fed:
            m.update(v, t)
        preds = []
        for tt in times.tolist():
            preds.append(m.forecast_at(tt))
            m.update(None, tt)
        out[name] = np.array(preds)
    return out


def spatial_estimate(ctx: SeedContext, sid: str, start: int, end: int, period: str) -> np.ndarray:
    g = ctx.grid(ctx.edge["edge_hw"].frame, period)
    j = g.col(sid)
    sus = g.suspect.copy()
    rows = (g.times >= start) & (g.times < end)
    sus[rows, j] = True
    r = ctx.spatial_val.apply(g.values, sus)
    per = ctx.tb.sensor(sid).period_s
    sel = rows & (g.times % per == 0)
    return r.estimate[sel, j]


def _errors(ctx: SeedContext, period: str, n_per: int, rng: np.random.Generator) -> pd.DataFrame:
    truth = ctx.tb.truth.set_index(["sensor_id", "time_s"]).value
    rows = []
    for gap, (sid, start, end) in enumerate(_gaps(ctx, period, n_per, rng)):
        s = ctx.tb.sensor(sid)
        times = np.arange(start, end, s.period_s)
        tv = truth.loc[sid].reindex(times).to_numpy()
        est = temporal_estimates(ctx, sid, start, end)
        est["spatial"] = spatial_estimate(ctx, sid, start, end, period)
        minutes = (end - start) // 60
        for m, v in est.items():
            e = v - tv
            rows.append(
                {
                    "gap": gap,
                    "sensor_id": sid,
                    "kind": s.kind,
                    "gap_min": int(minutes),
                    "method": m,
                    "rmse": float(np.sqrt(np.mean(e**2))),
                    "mae": float(np.mean(np.abs(e))),
                }
            )
    return pd.DataFrame(rows)


def crossover(calib: pd.DataFrame) -> int:
    """Smallest gap length at which the spatial estimate beats Holt-Winters on average."""
    piv = calib.groupby(["gap_min", "method"]).mae.mean().unstack()
    rel = piv["spatial"] / piv["holt_winters"]
    better = rel[rel <= 1.0]
    return int(better.index.min()) if len(better) else int(piv.index.max()) + 1


def e2(ctx: SeedContext) -> pd.DataFrame:
    rng = np.random.default_rng([ctx.seed, 21])
    n = ctx.cfg.experiments.gaps_per_length
    calib = _errors(ctx, "calib", max(3, n // 2), rng)
    # Normalise MAE per kind so one crossover serves all units.
    calib = calib.assign(mae=calib.mae / calib.groupby("kind").mae.transform("mean"))
    cross = crossover(calib)
    test = _errors(ctx, "test", n, rng)
    hyb = test[test.method == "holt_winters"].copy().set_index("gap")
    sp = test[test.method == "spatial"].set_index("gap")
    use_sp = hyb.gap_min >= cross
    for c in ("rmse", "mae"):
        hyb.loc[use_sp, c] = sp.loc[use_sp[use_sp].index, c]
    hyb = hyb.reset_index()
    hyb["method"] = "hybrid"
    out = pd.concat([test, hyb], ignore_index=True)
    return out.assign(seed=ctx.seed, crossover_min=cross)
