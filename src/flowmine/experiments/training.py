"""E5: local, centralised and federated training of the edge forecaster.

Run on the pressure loggers (the largest homogeneous sensor class). One station is
the cold start: it has only ``cold_start_days`` of history before the test week, the
others have the full training weeks. One-step error is scored on the test week's
clean samples against the noise-free truth, with the observed values as inputs;
day-ahead error is the recursive 24 h forecast from each midnight of the test week.
Errors are in metres. Bytes moved count the traffic each paradigm needs to train.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from flowmine.central import nn
from flowmine.experiments.context import SeedContext
from flowmine.hybrid import federated

DAY = 86400


def _series(ctx: SeedContext, sid: str, t0: int, t1: int, frame: str = "observed") -> np.ndarray:
    df = getattr(ctx.tb, frame)
    return df.loc[(df.sensor_id == sid) & (df.time_s >= t0) & (df.time_s < t1), "value"].to_numpy(
        dtype=float
    )


def _one_step(
    m: nn.MLP, obs: np.ndarray, truth: np.ndarray, clean: np.ndarray, slot0: int
) -> float:
    z = (obs - m.mean) / m.std
    ks, x = nn.feature_rows(z, m.window, m.s1, m.s2, slot0)
    ok = np.isfinite(x).all(axis=1) & clean[ks + 1] & np.isfinite(truth[ks + 1])
    pred = (z[ks[ok]] + nn.forward(m, x[ok])) * m.std + m.mean
    return float(np.sqrt(np.mean((pred - truth[ks[ok] + 1]) ** 2)))


def e5(ctx: SeedContext) -> pd.DataFrame:
    cfg = ctx.cfg
    ex = cfg.experiments
    pressure = [s.sensor_id for s in ctx.tb.sensors if s.kind == "pressure"]
    cold = pressure[0]
    period = 300
    train_end = ctx.periods["train"][1]
    test0, test1 = ctx.periods["test"]
    series = {}
    slot0 = {}
    for sid in pressure:
        start = train_end - ex.cold_start_days * DAY if sid == cold else 0
        series[sid] = _series(ctx, sid, start, train_end)
        slot0[sid] = start // period
    fed = (ex.fed_rounds, ex.fed_local_epochs)
    paradigms = {
        "local": federated.local(series, cfg, period, ctx.seed, slot0),
        "centralised": federated.centralised(series, cfg, period, ctx.seed, slot0),
        "federated": federated.federated(
            series, cfg, period, ctx.seed, *fed, fine_tune=False, slot0=slot0
        ),
        "federated_finetuned": federated.federated(
            series, cfg, period, ctx.seed, *fed, fine_tune=True, slot0=slot0
        ),
    }
    lab = ctx.tb.labels
    hist0 = test0 - DAY
    rows = []
    for name, tr in paradigms.items():
        for sid in pressure:
            m = tr.nets[sid]
            obs = _series(ctx, sid, hist0, test1)
            truth = _series(ctx, sid, hist0, test1, "truth")
            lb = lab[(lab.sensor_id == sid) & (lab.time_s >= hist0) & (lab.time_s < test1)]
            clean = ((lb.fault_type == "") & (lb.event_id < 0)).to_numpy().copy()
            clean[: DAY // period] = False
            one = _one_step(m, obs, truth, clean, hist0 // period)
            # Origin d is the last slot before test midnight d; its forecast covers that
            # whole day, so it needs one history day plus d + 1 days of series.
            per_day = DAY // period
            origins = [
                per_day - 1 + d * per_day
                for d in range(ex.day_ahead_origins)
                if per_day * (d + 2) <= len(obs)
            ]
            obs_filled = pd.Series(obs).interpolate(limit_direction="both").to_numpy()
            day = federated.day_ahead_rmse(m, obs_filled, truth, origins, hist0 // period)
            rows.append(
                {
                    "seed": ctx.seed,
                    "paradigm": name,
                    "sensor_id": sid,
                    "cold_start": sid == cold,
                    "one_step_rmse_m": one,
                    "day_ahead_rmse_m": day,
                    "bytes_up": tr.bytes_up,
                    "bytes_down": tr.bytes_down,
                    "parameters": m.n_params,
                }
            )
    return pd.DataFrame(rows)
