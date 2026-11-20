"""Run E1-E13 (plus the maintenance-log and temperature checks) over all seeds.

Each seed runs in its own process. The BLAS thread variables are set before the
workers start (the Makefile also exports them), so every worker runs single-threaded
numerics and results do not depend on scheduling. Per-seed rows go to
``<out>/raw/<experiment>.csv``; the report step aggregates them. Floats are written
with a fixed format so that a rerun gives byte-identical files.
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

from flowmine.central import external
from flowmine.config import Config
from flowmine.experiments.context import SeedContext

log = logging.getLogger(__name__)
EXPERIMENTS = (
    "e1_detection",
    "e2_reconstruction",
    "e3_policies",
    "e3_epsilon",
    "e4_outage",
    "e5_training",
    "e6_twin",
    "e7_events",
    "x1_maintenance",
    "x2_temperature",
    "e8a_conformal",
    "e8b_intervals",
    "e8b_tables",
    "e8c_ablation",
    "e8_xmodels",
    "e8_cusum",
    "e8d_int8",
)


def _single_thread() -> None:
    for var in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    ):
        os.environ[var] = "1"


def temperature_check(ctx: SeedContext) -> pd.DataFrame:
    tb = ctx.tb
    wide = tb.wide("observed")
    sources = ["F-10", "F-60"]
    inlets = {
        sid: next(st.inflow_sign for st in tb.stations if sid in st.sensors)
        for sid in ("F-20", "F-40", "F-50")
    }
    dem = external.hourly_total_demand(wide, sources, inlets)
    temp = tb.weather.set_index((tb.weather.time_s // 3600).astype(int)).air_temperature_c
    p = ctx.periods
    s = external.score(
        dem, temp, p["train"][1] // 3600, (p["test"][0] // 3600, p["test"][1] // 3600)
    )
    return pd.DataFrame(
        [
            {
                "seed": ctx.seed,
                "mae_without_temperature_l_s": s.mae_without,
                "mae_with_temperature_l_s": s.mae_with,
                "mean_demand_l_s": s.mean_demand,
            }
        ]
    )


def run_seed(cfg: Config, seed: int) -> dict[str, pd.DataFrame]:
    from flowmine.experiments import (
        comms,
        context,
        detection,
        edge_v2,
        reconstruction,
        training,
        twin_value,
    )
    from flowmine.testbed import network

    logging.basicConfig(level=logging.WARNING)
    ctx = context.build_context(cfg, seed)
    out: dict[str, pd.DataFrame] = {}
    out["e1_detection"], _ = detection.e1(ctx)
    out["e2_reconstruction"] = reconstruction.e2(ctx)
    out["e3_policies"], out["e3_epsilon"] = comms.e3(ctx)
    out["e4_outage"] = comms.e4(ctx)
    out["e5_training"] = training.e5(ctx)
    out["e6_twin"] = twin_value.e6(ctx)
    out["e7_events"] = detection.e7(ctx)
    wn = network.load_network(cfg.network.inp)
    out["x1_maintenance"] = detection.maintenance_ablation(ctx, wn, network.graph(wn))
    out["x2_temperature"] = temperature_check(ctx)
    out.update(edge_v2.e8(ctx))
    return out


def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, float_format="%.6g", lineterminator="\n")


def run_all(cfg: Config, out: Path) -> None:
    _single_thread()
    seeds = list(cfg.seeds)
    workers = max(1, min(cfg.experiments.workers, len(seeds)))
    if workers == 1:
        results = [run_seed(cfg, s) for s in seeds]
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(run_seed, [cfg] * len(seeds), seeds))
    for name in EXPERIMENTS:
        df = pd.concat([r[name] for r in results], ignore_index=True)
        write_csv(df, out / "raw" / f"{name}.csv")
    log.info("wrote %d experiment tables for seeds %s to %s", len(EXPERIMENTS), seeds, out)
