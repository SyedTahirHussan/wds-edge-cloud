"""E10: bulk chlorine decay coefficient estimated from laboratory and analyser data.

Hydraulic model: the twin's demand model run as one extended-period simulation over
all eight weeks (base demands times one multiplier, the total demand divided by the
sum of base demands, as the bridge's water balance gives it; the process events are
not in the model). Water quality: first-order bulk decay with coefficient kb and the
model's wall coefficient (zero in Net3.inp), sources and tanks at the configured
concentrations. The testbed's truth uses kb = -0.5 /day and a first-order wall
coefficient of -0.05 m/day, so a bulk-only model must absorb the wall decay into kb.

Estimator: a moving-window least-squares fit. EPANET runs once per kb on a grid
(``KB_GRID``); the simulated concentration of each sample is interpolated linearly in
kb between grid points. Every day at 00:00, kb is the value that minimises the sum of
squared differences between observations and simulation over the samples of the last
``WINDOW_DAYS`` days whose result is available at that moment (laboratory results
only after their reporting time; analyser hourly means at once). Two variants: ``lab``
(grab samples only) and ``lab_analyser`` (plus the analysers' hourly means, from the
raw samples in weeks 1-6 and the edge-validated stream in week 7, flagged samples
left out). The estimate at the start of week 8 is the one scored.

Scored on week 8 (EPANET with the coefficients fixed): mean absolute error of
simulated chlorine against the true concentration at the analyser nodes, and against
the week-8 laboratory results at the sampling points.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import wntr

from flowmine.config import Config
from flowmine.edge.core import quality
from flowmine.experiments.context import SeedContext
from flowmine.testbed import network, simulate

DAY = 86400
KB_GRID = (0.0, -0.2, -0.4, -0.6, -0.8, -1.0, -1.2, -1.4, -1.6, -1.8, -2.0, -2.5, -3.0)  # 1/day
WINDOW_DAYS = 14


def model_network(cfg: Config, total_l_s: np.ndarray) -> tuple[wntr.network.WaterNetworkModel, int]:
    """The twin's network with one global multiplier pattern over the whole horizon."""
    duration = cfg.total_days * DAY
    wn = network.load_network(cfg.network.inp)
    network.convert_time_controls(wn)
    step = cfg.network.hydraulic_step_s
    n_steps = duration // step + 1
    base = simulate.assign_demands(wn, np.ones((n_steps, len(wn.junction_name_list))))
    network.configure(wn, cfg, duration)
    mult = list(np.asarray(total_l_s, dtype=float)[:n_steps] / (1000.0 * sum(base.values())))
    for j in wn.junction_name_list:
        if f"m_{j}" in wn.pattern_name_list:
            wn.get_pattern(f"m_{j}").multipliers = mult
    return wn, n_steps


def run_quality(
    wn: wntr.network.WaterNetworkModel, kb_per_day: float, kw_m_per_day: float, nodes: list[str]
) -> pd.DataFrame:
    """time_s x node chlorine (mg/L) for the given coefficients."""
    r = wn.options.reaction
    r.bulk_coeff = kb_per_day / DAY
    r.wall_coeff = kw_m_per_day / DAY
    res = simulate.run(wn)
    return (res.node["quality"][nodes] * 1000.0).astype(float)


def observations(ctx: SeedContext) -> pd.DataFrame:
    """Rows: kind (lab | analyser), node, time_s (phenomenon), avail_s, value."""
    tb = ctx.tb
    c0, c1 = ctx.periods["calib"]
    lab = tb.lab
    rows = [
        pd.DataFrame(
            {
                "kind": "lab",
                "node": lab.node.astype(str),
                "time_s": lab.phenomenon_s,
                "avail_s": lab.result_s,
                "value": lab.value,
            }
        )
    ]
    node_of = {s.sensor_id: s.element for s in tb.sensors if s.kind == "chlorine"}
    obs = tb.observed[tb.observed.sensor_id.isin(node_of) & (tb.observed.time_s < c0)]
    ef = ctx.edge["edge_hw"].frame
    ef = ef[ef.stream.isin(node_of) & (ef.time_s >= c0) & (ef.time_s < c1)]
    q = ef.quality.to_numpy(dtype=np.int64)
    bad = (
        q & (quality.MISSING | quality.REJECTING | quality.CROSSCHECK | quality.RECONSTRUCTED)
    ) != 0
    an = pd.concat(
        [
            obs.rename(columns={"sensor_id": "stream"})[["stream", "time_s", "value"]],
            ef[~bad][["stream", "time_s", "value"]],
        ]
    )
    an = an.assign(value=pd.to_numeric(an.value, errors="coerce")).dropna()
    an["hour"] = an.time_s // 3600 * 3600
    hourly = an.groupby(["stream", "hour"]).value.mean().reset_index()
    rows.append(
        pd.DataFrame(
            {
                "kind": "analyser",
                "node": hourly.stream.map(node_of),
                "time_s": hourly.hour + 1800,
                "avail_s": hourly.hour + 3600,
                "value": hourly.value,
            }
        )
    )
    return pd.concat(rows, ignore_index=True)


def interpolated(sims: dict[float, pd.DataFrame], obs: pd.DataFrame) -> np.ndarray:
    """Simulated value of each observation on the kb grid, shape (n_obs, n_grid)."""
    step = 300
    out = np.empty((len(obs), len(KB_GRID)))
    idx = (obs.time_s.to_numpy() // step).astype(int)
    for g, kb in enumerate(KB_GRID):
        s = sims[kb]
        arr = s.to_numpy()
        col = {n: i for i, n in enumerate(s.columns)}
        out[:, g] = arr[np.minimum(idx, len(arr) - 1), [col[n] for n in obs.node]]
    return out


def interp_weights(fine: np.ndarray) -> np.ndarray:
    """(n_grid, n_fine) weights of linear interpolation in kb from KB_GRID to ``fine``."""
    grid = np.array(KB_GRID)
    w = np.zeros((len(grid), len(fine)))
    for k, kb in enumerate(fine):
        e = np.zeros(len(grid))
        for g in range(len(grid)):
            e[:] = 0.0
            e[g] = 1.0
            order = np.argsort(grid)
            w[g, k] = np.interp(kb, grid[order], e[order])
    return w


def fit(pred: np.ndarray, y: np.ndarray, fine: np.ndarray, w: np.ndarray) -> float:
    """kb on the fine grid minimising squared error (linear interpolation in kb)."""
    sse = (((pred @ w) - y[:, None]) ** 2).sum(axis=0)
    return float(fine[int(np.argmin(sse))])


def e10(ctx: SeedContext) -> dict[str, pd.DataFrame]:
    cfg = ctx.cfg
    tb = ctx.tb
    dem = tb.demand
    total = dem.sum(axis=1).to_numpy(dtype=float)
    wn, _ = model_network(cfg, total)
    nodes = sorted({s.element for s in tb.sensors if s.kind == "chlorine"})
    kw_model = 0.0
    sims = {kb: run_quality(wn, kb, kw_model, nodes) for kb in KB_GRID}
    obs = observations(ctx)
    pred = interpolated(sims, obs)
    fine = np.round(np.arange(0.0, -3.0001, -0.01), 2)
    w = interp_weights(fine)
    t_test = ctx.periods["test"][0]
    traj = []
    estimate: dict[str, float] = {}
    for day in range(2, cfg.total_days - cfg.network.test_days + 1):
        t = day * DAY
        for variant in ("lab", "lab_analyser"):
            sel = (obs.avail_s <= t) & (obs.time_s >= t - WINDOW_DAYS * DAY)
            if variant == "lab":
                sel &= obs.kind == "lab"
            n = int(sel.sum())
            if not (obs[sel].kind == "lab").any():
                continue
            kb = fit(pred[sel.to_numpy()], obs.value.to_numpy()[sel.to_numpy()], fine, w)
            traj.append(
                {"seed": ctx.seed, "day": day, "variant": variant, "kb_per_day": kb, "n": n}
            )
            if t == t_test:
                estimate[variant] = kb
    true_kb = cfg.quality.bulk_coeff_per_day
    true_kw = cfg.quality.wall_coeff_m_per_day
    r0 = wn.options.reaction
    w0 = network.load_network(cfg.network.inp).options.reaction
    cases = {
        "default (Net3.inp coefficients)": (w0.bulk_coeff * DAY, w0.wall_coeff * DAY),
        "estimated, lab": (estimate.get("lab", np.nan), kw_model),
        "estimated, lab + analysers": (estimate.get("lab_analyser", np.nan), kw_model),
        "true kb, model wall": (true_kb, kw_model),
        "true kb and wall": (true_kb, true_kw),
    }
    del r0
    t0, t1 = ctx.periods["test"]
    truth = tb.nodes_cl
    tt = truth.index[(truth.index >= t0) & (truth.index < t1)]
    lab8 = tb.lab[(tb.lab.phenomenon_s >= t0) & (tb.lab.phenomenon_s < t1)]
    rows = []
    for name, (kb, kw) in cases.items():
        if not np.isfinite(kb):
            continue
        sim = run_quality(wn, float(kb), float(kw), nodes)
        err_a = (sim.reindex(tt)[nodes] - truth.loc[tt, nodes]).abs().to_numpy()
        lab_sim = sim.to_numpy()[
            (lab8.phenomenon_s.to_numpy() // 300).astype(int),
            [nodes.index(str(n)) for n in lab8.node],
        ]
        rows.append(
            {
                "seed": ctx.seed,
                "case": name,
                "kb_per_day": float(kb),
                "kw_m_per_day": float(kw),
                "mae_analyser_nodes_mg_l": float(np.nanmean(err_a)),
                "mae_lab_points_mg_l": float(np.mean(np.abs(lab_sim - lab8.value.to_numpy()))),
                "n_lab": len(lab8),
            }
        )
    return {"e10_chlorine": pd.DataFrame(rows), "e10_kb_trajectory": pd.DataFrame(traj)}
