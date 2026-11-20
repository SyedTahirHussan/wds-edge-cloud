"""Model-based validation level: the twin's pressure residual as a central validator.

The twin is driven by the edge-validated boundary streams (tank levels, pump and
source flows), as in the deployed hybrid design, and its simulated pressure at each
pressure sensor is compared with the sample as measured. Per sensor, the residual is
centred by its week-7 median for each hour of day (the single demand multiplier
leaves a daily residual pattern) and a split conformal threshold is taken from the
week-7 clean samples:

    s_t = |r_t - mu(h_t)|,   q = the ceil((n + 1)(1 - alpha))-th smallest week-7 score

A test-week sample is flagged when s_t > q. Two residuals are tried: the plain one
("plain") and one with the common mode removed ("common_mode": minus the median
residual over the pressure sensors at that time), because an error in a boundary
input (a flow-meter bias, say) moves every pressure residual together. The variant
with the higher recall on the labelled week-7 faults at the band's level is used
(both bands come from week-7 clean samples). The level only covers pressure sensors,
and a leak or a faulty boundary meter moves the residual as well, so it cannot by
itself tell a leak from a sensor fault.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from flowmine.experiments.context import SeedContext
from flowmine.sta.provision import thing_body
from flowmine.testbed import network
from flowmine.twin.bridge import sensors_from_things
from flowmine.twin.runner import run_twin

H = 3600
DAY = 86400


def pressure_ids(ctx: SeedContext) -> list[str]:
    return [s.sensor_id for s in ctx.tb.sensors if s.kind == "pressure"]


def residuals(ctx: SeedContext, period: str) -> pd.DataFrame:
    """time_s x sensor_id: observed pressure minus the twin's (edge-validated inputs)."""
    key = f"model_residual_{period}"
    if key in ctx.cache:
        out = ctx.cache[key]
        assert isinstance(out, pd.DataFrame)
        return out
    tb = ctx.tb
    t0, t1 = ctx.periods[period]
    ids = pressure_ids(ctx)
    things = [
        thing_body(st, [s for s in tb.sensors if s.station == st.station_id], "")
        for st in tb.stations
    ]
    times = np.arange(t0, t1, ctx.cfg.network.hydraulic_step_s)
    measured = tb.wide("observed").reindex(index=times, columns=ids)
    wn = network.load_network(ctx.cfg.network.inp)
    run = run_twin(
        ctx.cfg, wn, sensors_from_things(things), ctx.edge["edge_hw"].frame, t0, t1, [], measured
    )
    res = run.residual.rename(columns=lambda c: c[len("RES-") :])[ids]
    ctx.cache[key] = res
    return res


VARIANTS = ("plain", "common_mode")


def variant_residuals(ctx: SeedContext, period: str, variant: str) -> pd.DataFrame:
    res = residuals(ctx, period)
    if variant == "plain":
        return res
    return res.sub(res.median(axis=1), axis=0)


def conformal_q(scores: np.ndarray, alpha: float) -> float:
    s = np.sort(scores[np.isfinite(scores)])
    n = len(s)
    k = math.ceil((n + 1) * (1 - alpha))
    return float(s[k - 1]) if 1 <= k <= n else math.inf


def clean_wide(ctx: SeedContext, ids: list[str], times: np.ndarray) -> pd.DataFrame:
    lab = ctx.tb.labels
    sub = lab[lab.sensor_id.isin(ids) & lab.time_s.isin(times)]
    ok = (sub.fault_type == "") & (sub.event_id < 0)
    w = sub.assign(ok=ok).pivot(index="time_s", columns="sensor_id", values="ok")
    return w.reindex(index=times, columns=ids).fillna(False).astype(bool)


def bands(ctx: SeedContext, variant: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Hourly centres (24 x sensors) and per-sensor thresholds from week 7."""
    alpha = ctx.cfg.edge.conformal_alpha
    res = variant_residuals(ctx, "calib", variant)
    ids = list(res.columns)
    ok = clean_wide(ctx, ids, res.index.to_numpy())
    clean = res.where(ok)
    hour = (res.index.to_numpy() % DAY) // H
    mu = clean.groupby(hour).median().reindex(range(24))
    score = (res - mu.loc[hour].to_numpy()).abs().where(ok)
    q = pd.DataFrame(
        {
            "sensor_id": ids,
            "q_m": [conformal_q(score[s].to_numpy(), alpha) for s in ids],
            "n_calib": [int(np.isfinite(score[s]).sum()) for s in ids],
        }
    )
    return mu, q


def raw_flags(ctx: SeedContext, period: str, variant: str) -> pd.DataFrame:
    mu, q = bands(ctx, variant)
    res = variant_residuals(ctx, period, variant)
    hour = (res.index.to_numpy() % DAY) // H
    score = (res - mu.loc[hour].to_numpy()).abs()
    ratio = score.div(q.set_index("sensor_id").q_m.reindex(res.columns), axis=1)
    long = ratio.stack(future_stack=True).reset_index()
    long.columns = pd.Index(["time_s", "sensor_id", "model_ratio"])
    long["model_flag"] = (long.model_ratio > 1.0).fillna(False).astype(bool)
    return long


def calib_recall(ctx: SeedContext, variant: str) -> float:
    """Share of week-7 non-gap fault samples at the pressure sensors that are flagged."""
    f = raw_flags(ctx, "calib", variant)
    lab = ctx.tb.labels
    bad = lab[(lab.fault_type != "") & ~lab.fault_type.isin(["gap_short", "gap_long"])]
    m = f.merge(bad[["sensor_id", "time_s"]], on=["sensor_id", "time_s"])
    return float(m.model_flag.mean()) if len(m) else 0.0


def chosen_variant(ctx: SeedContext) -> str:
    if "model_variant" not in ctx.cache:
        rec = {v: calib_recall(ctx, v) for v in VARIANTS}
        ctx.cache["model_variant"] = max(VARIANTS, key=lambda v: (rec[v], v == "plain"))
        ctx.cache["model_calib_recall"] = rec
    return str(ctx.cache["model_variant"])


def flags(ctx: SeedContext, period: str = "test") -> pd.DataFrame:
    """Long frame (sensor_id, time_s, model_flag, model_ratio) for the pressure sensors,
    with the variant chosen on week 7."""
    return raw_flags(ctx, period, chosen_variant(ctx))


def band_table(ctx: SeedContext) -> pd.DataFrame:
    """Per variant and sensor: threshold, week-7 fault recall, and the share of clean
    samples outside the band (weeks 7, 8)."""
    chosen = chosen_variant(ctx)
    rows = []
    for variant, period in [(v, p) for v in VARIANTS for p in ("calib", "test")]:
        _, q = bands(ctx, variant)
        f = raw_flags(ctx, period, variant)
        ids = list(q.sensor_id)
        ok = clean_wide(ctx, ids, np.unique(f.time_s.to_numpy()))
        okl = ok.stack().reset_index()
        okl.columns = pd.Index(["time_s", "sensor_id", "clean"])
        m = f.merge(okl, on=["time_s", "sensor_id"])
        m = m[m.clean & m.model_ratio.notna()]
        rate = m.groupby("sensor_id").model_flag.mean()
        for s in ids:
            rows.append(
                {
                    "seed": ctx.seed,
                    "variant": variant,
                    "chosen": int(variant == chosen),
                    "calib_fault_recall": ctx.cache["model_calib_recall"][variant],  # type: ignore[index]
                    "sensor_id": s,
                    "period": period,
                    "alpha": ctx.cfg.edge.conformal_alpha,
                    "q_m": float(q.set_index("sensor_id").q_m[s]),
                    "clean_flag_rate": float(rate.get(s, np.nan)),
                }
            )
    return pd.DataFrame(rows)
