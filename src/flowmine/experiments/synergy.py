"""E12, edge to centre: what the central spatial model gains from the station's flags.

The central spatial model (ridge on the other sensors, central/spatial.py) runs on the
raw uplink and on the edge-validated stream (rejected samples left out, reconstructed
ones used). Both are calibrated on week 7 on their own input; week 8 is scored on the
hydraulic sensors with E1's metrics. The other edge-to-centre comparisons already
exist and are cited from their CSVs: the twin (E6, raw against edge-validated input)
and leak localisation (E9). The centre-to-edge direction is covered by E5 (models
trained in the cloud for a cold-start station); the calibration-offset loop was not
built (see .worker/REPORT4.md).
"""

from __future__ import annotations

import pandas as pd

from flowmine.experiments import detection
from flowmine.experiments.context import SeedContext

METHODS = ("spatial_raw", "spatial_validated")


def e12(ctx: SeedContext, flags: pd.DataFrame) -> pd.DataFrame:
    hyd = flags[~flags.sensor_id.isin(ctx.chlorine_ids)].reset_index(drop=True)
    miss = hyd.missing.to_numpy()
    hyd = hyd.assign(
        spatial_raw=(hyd.central_ratio.to_numpy(dtype=float) > 1.0) | miss,
        spatial_validated=(hyd.hybrid_ratio.to_numpy(dtype=float) > 1.0) | miss,
    )
    return detection.score(ctx, hyd, METHODS, list(ctx.hydraulic)).assign(seed=ctx.seed)
