"""E9: leak localisation from the twin's pressure residuals.

Cases per seed: the testbed's own test-week leak (the full pipeline: stations, gateway,
twin) and ``leak_cases`` synthetic leaks at random junctions, one at a time, with the
sizes in ``leak_areas_m2``. A synthetic case is made by superposition: EPANET runs
the test week with the true demands and the non-leak process events, once without and
once with the case's leak (the testbed's leak equation, ramped over ``leak_ramp_h``);
the difference at every sensor's element is added to each input frame (raw,
edge-validated, hybrid-validated, clean). The flags of the validated frames are those
of the leak-free run, so the stations' own reaction to the leak is not modelled.
Synthetic onsets fall before the testbed's leak starts, so the two do not overlap.

For each case and input the twin runs from 80 h before onset to 12 h after it (the
look-back reaches into week 7 for early test-week onsets). The residual vector is the
mean residual over [onset + 6 h, onset + 12 h), with one of three baselines removed:
"none"; "day1", the mean over the same hours one day earlier; "day3", the mean over
the same hours of the three previous days (the twin's single demand multiplier leaves
a residual that repeats daily). Variants (baseline x correlation/angle) are compared
on synthetic week-7 cases with edge-validated input (``e9_leak_calib``); the report
names the operating variant from week 7 only and shows all of them for week 8.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import wntr

from flowmine.central import homogenise, leak
from flowmine.edge.core import quality
from flowmine.experiments.context import SeedContext
from flowmine.experiments.twin_value import hybrid_cached
from flowmine.sta.provision import thing_body
from flowmine.testbed import build, network, simulate
from flowmine.testbed.sensors import Sensor
from flowmine.twin.bridge import sensors_from_things
from flowmine.twin.runner import run_twin

H = 3600
DAY = 86400
INPUTS = ("raw", "edge_validated", "hybrid_validated", "clean")
METHODS = ("correlation", "angle")
BASELINES = ("none", "day1", "day3")
LOOKBACK_H = 80


def monitored_sensors(ctx: SeedContext) -> list[tuple[str, str]]:
    """(sensor id, node) of every pressure sensor, in sensor order."""
    return [(s.sensor_id, s.element) for s in ctx.tb.sensors if s.kind == "pressure"]


def sensitivity(ctx: SeedContext) -> leak.Sensitivity:
    """Sensitivity matrix of this seed's twin, cached next to the testbed."""
    if "sensitivity" in ctx.cache:
        return ctx.cache["sensitivity"]  # type: ignore[return-value]
    tb = ctx.tb
    t_end = ctx.periods["train"][1]
    dem = tb.demand
    junc = [c for c in dem.columns if not str(c).startswith("LEAK-")]
    d = dem[dem.index < t_end]
    total = d[junc].sum(axis=1).to_numpy(dtype=float)
    mult = leak.daily_multiplier(total, d.index.to_numpy(), 1000.0 * sum(tb.base_demand.values()))
    root = Path("data") / f"{ctx.cfg.name}-{ctx.cfg.digest()}" / f"seed{ctx.seed}"
    sens = leak.ensure(
        ctx.cfg,
        [n for _, n in monitored_sensors(ctx)],
        mult,
        root,
        ctx.cfg.experiments.leak_nominal_l_s,
    )
    ctx.cache["sensitivity"] = sens
    return sens


def plan_cases(ctx: SeedContext, period: str = "test") -> pd.DataFrame:
    """Synthetic cases (seeded) plus the testbed's own leaks of the period.

    Test week: onsets from 26 h after the start (the look-back reaches into week 7) to
    12 h before the testbed's leak. Week 7 has no testbed leak; onsets start late
    enough for the look-back to stay inside week 7, where the edge frames exist.
    """
    cfg = ctx.cfg
    t0, t1 = ctx.periods[period]
    ev = ctx.tb.events
    own = ev[(ev.period == period) & (ev.event_type == "leak")]
    tail = int(cfg.experiments.leak_window_h[1] * H)
    lo = t0 + (26 * H if period == "test" else LOOKBACK_H * H)
    hi = (int(own.start_s.min()) if len(own) else t1) - tail
    if hi <= lo:  # short periods (smoke): allow overlap with the testbed leak
        lo, hi = t0 + 26 * H, t1 - tail
    rng = np.random.default_rng([ctx.seed, 91 if period == "test" else 92])
    wn = network.load_network(cfg.network.inp)
    nodes = sorted(wn.junction_name_list)
    n = cfg.experiments.leak_cases
    pick = rng.choice(nodes, size=min(n, len(nodes)), replace=False)
    onsets = (rng.uniform(lo, hi, size=len(pick)) // 300 * 300).astype(int)
    areas = cfg.experiments.leak_areas_m2
    rows = [
        {
            "case_id": k,
            "kind": "synthetic",
            "node": str(j),
            "pipe": "",
            "area_m2": float(areas[k % len(areas)]),
            "onset_s": int(o),
        }
        for k, (j, o) in enumerate(zip(pick, onsets, strict=True))
    ]
    for _, e in own.iterrows():
        rows.append(
            {
                "case_id": len(rows),
                "kind": "testbed",
                "node": f"LEAK-{e.element}",
                "pipe": str(e.element),
                "area_m2": float(e["size"]),
                "onset_s": int(e.start_s),
            }
        )
    return pd.DataFrame(rows)


def truth_network(ctx: SeedContext, duration_s: int) -> tuple[wntr.network.WaterNetworkModel, int]:
    """The testbed's network with true demands and the non-leak process events."""
    cfg = ctx.cfg
    wn, _, _, _ = simulate.build_network(cfg, build.rng_for(ctx.seed, "demand"), cfg.total_days)
    events = [
        simulate.ProcessEvent(**r)  # type: ignore[arg-type]
        for r in ctx.tb.events.to_dict("records")
        if r["event_type"] != "leak"
    ]
    n_steps = cfg.total_days * DAY // cfg.network.hydraulic_step_s + 1
    simulate.apply_events(wn, events, pd.DataFrame(), cfg, n_steps)
    wn.options.time.duration = duration_s
    wn.options.quality.parameter = "NONE"
    return wn, n_steps


def sensor_values(res: wntr.sim.SimulationResults, sensors: list[Sensor]) -> pd.DataFrame:
    """time_s x sensor_id of the quantity each hydraulic sensor measures."""
    cols = {}
    for s in sensors:
        if s.kind == "flow":
            cols[s.sensor_id] = res.link["flowrate"][s.element] * 1000.0
        elif s.kind in ("pressure", "level"):
            cols[s.sensor_id] = res.node["pressure"][s.element]
    return pd.DataFrame(cols)


def deltas(ctx: SeedContext, cases: pd.DataFrame) -> dict[int, pd.DataFrame]:
    """Per synthetic case: the leak's effect at every hydraulic sensor (case - base)."""
    syn = cases[cases.kind == "synthetic"]
    if syn.empty:
        return {}
    cfg = ctx.cfg
    end = int(syn.onset_s.max()) + int(cfg.experiments.leak_window_h[1] * H) + H
    wn, n_steps = truth_network(ctx, end)
    base = simulate.run(wn)
    p_base = base.node["pressure"]
    v_base = sensor_values(base, ctx.tb.sensors)
    step = cfg.network.hydraulic_step_s
    t = np.arange(n_steps) * step
    ev = cfg.events
    out = {}
    wn.add_pattern("case_leak", [0.0])
    for _, c in syn.iterrows():
        p = p_base[c.node].reindex(t).ffill().to_numpy()
        q = ev.leak_discharge_coeff * c.area_m2 * np.sqrt(2 * simulate.G * np.clip(p, 0.0, None))
        ramp = np.clip((t - c.onset_s) / (ev.leak_ramp_h * H), 0.0, 1.0)
        wn.get_pattern("case_leak").multipliers = list(q * ramp)
        node = wn.get_node(c.node)
        node.add_demand(1.0, "case_leak")
        res = simulate.run(wn)
        del node.demand_timeseries_list[-1]
        out[int(c.case_id)] = sensor_values(res, ctx.tb.sensors) - v_base
    return out


def shifted(frame: pd.DataFrame, delta: pd.DataFrame | None, w0: int, w1: int) -> pd.DataFrame:
    """Window of a tidy frame with the leak's effect added to each stream."""
    f = frame[(frame.time_s >= w0) & (frame.time_s < w1)].copy()
    if delta is None:
        return f
    long = delta.stack().reset_index()
    long.columns = pd.Index(["time_s", "stream", "d"])
    m = f.merge(long, on=["stream", "time_s"], how="left")
    m["value"] = pd.to_numeric(m.value, errors="coerce") + m.d.fillna(0.0)
    return m.drop(columns="d")


def measured_pressure(frame: pd.DataFrame, streams: list[str], times: np.ndarray) -> pd.DataFrame:
    """Usable pressure values (unflagged or reconstructed), NaN elsewhere."""
    sub = frame[frame.stream.isin(streams)]
    q = sub.quality.to_numpy(dtype=np.int64)
    bad = (q & (quality.MISSING | quality.REJECTING | quality.CROSSCHECK)) != 0
    rec = (q & quality.RECONSTRUCTED) != 0
    sub = sub.assign(value=np.where(bad & ~rec, np.nan, pd.to_numeric(sub.value, errors="coerce")))
    w = sub.pivot_table(index="time_s", columns="stream", values="value", aggfunc="last")
    return w.reindex(index=times, columns=streams)


def _mean(res: pd.DataFrame, cols: list[str], a: int, b: int) -> np.ndarray:
    sub = res.loc[(res.index >= a) & (res.index < b), cols].to_numpy(dtype=float)
    if not len(sub) or not np.isfinite(sub).any():
        return np.full(len(cols), np.nan)
    with np.errstate(invalid="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.asarray(np.nanmean(sub, axis=0))


def residual_vectors(
    res: pd.DataFrame, ids: list[str], onset: int, win: tuple[float, float]
) -> tuple[dict[str, np.ndarray], np.ndarray]:
    """Residual vector per baseline variant, and the hours of day of the window."""
    a, b = onset + int(win[0] * H), onset + int(win[1] * H)
    cols = [f"RES-{s}" for s in ids]
    post = _mean(res, cols, a, b)
    days = [_mean(res, cols, a - k * DAY, b - k * DAY) for k in (1, 2, 3)]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        day3 = np.nanmean(np.vstack(days), axis=0)
    hours = (np.arange(a, b, 300) % DAY) // H
    return {"none": post, "day1": post - days[0], "day3": post - day3}, hours


def input_frames(ctx: SeedContext) -> dict[str, pd.DataFrame]:
    """The four inputs over weeks 7 and 8 (hybrid fixes exist for week 8 only)."""
    tb = ctx.tb
    c0, _ = ctx.periods["calib"]
    t0, t1 = ctx.periods["test"]
    edge = ctx.edge["edge_hw"].frame
    hyb = hybrid_cached(ctx)
    return {
        "raw": homogenise.raw_frame(tb.observed, c0, t1),
        "edge_validated": edge,
        "hybrid_validated": pd.concat([edge[edge.time_s < t0], hyb], ignore_index=True),
        "clean": tb.truth.rename(columns={"sensor_id": "stream"}).assign(quality=0),
    }


def e9(ctx: SeedContext) -> dict[str, pd.DataFrame]:
    return {
        "e9_leak": localise(ctx, "test", INPUTS),
        "e9_leak_calib": localise(ctx, "calib", ("edge_validated",)),
    }


def localise(ctx: SeedContext, period: str, inputs: tuple[str, ...]) -> pd.DataFrame:
    cfg = ctx.cfg
    tb = ctx.tb
    p0, _ = ctx.periods["calib"]
    sens = sensitivity(ctx)
    mon = monitored_sensors(ctx)
    ids = [s for s, _ in mon]
    things = [
        thing_body(st, [s for s in tb.sensors if s.station == st.station_id], "")
        for st in tb.stations
    ]
    bsensors = sensors_from_things(things)
    wn = network.load_network(cfg.network.inp)
    g = network.graph(wn)
    dist = leak.distances(g, sens.candidates)
    cases = plan_cases(ctx, period)
    delta = deltas(ctx, cases)
    frames = input_frames(ctx)
    win = cfg.experiments.leak_window_h
    step = cfg.network.hydraulic_step_s
    rows = []
    for _, c in cases.iterrows():
        onset = int(c.onset_s)
        w0 = max(p0, onset - LOOKBACK_H * H)
        w1 = onset + int(win[1] * H)
        times = np.arange(w0, w1, step)
        if c.kind == "testbed":
            link = wn.get_link(str(c["pipe"]))
            ends = [link.start_node_name, link.end_node_name]
            half = float(link.length) / 2.0
        else:
            ends, half = [c.node], 0.0
        for name in inputs:
            f = shifted(frames[name], delta.get(int(c.case_id)), w0, w1)
            meas = measured_pressure(f, ids, times)
            run = run_twin(cfg, wn, bsensors, f, w0, w1, [], meas)
            vecs, hours = residual_vectors(run.residual, ids, onset, win)
            for base_name in BASELINES:
                r = vecs[base_name]
                for method in METHODS:
                    order, sc = leak.ranking(sens, r, hours, method)
                    top = order[0]
                    d = min(dist[top].get(e, np.inf) for e in ends) + half
                    row = {
                        "seed": ctx.seed,
                        "period": period,
                        "case_id": int(c.case_id),
                        "kind": c.kind,
                        "true_node": c.node,
                        "area_m2": c.area_m2,
                        "onset_s": onset,
                        "input": name,
                        "baseline": base_name,
                        "method": method,
                        "rank": leak.rank_of(order, ends),
                        "distance_m": d,
                        "n_candidates": len(order),
                        "n_residuals": int(np.isfinite(r).sum()),
                        "residual_norm_m": float(np.sqrt(np.nansum(r**2))),
                    }
                    for k in range(5):
                        row[f"top{k + 1}"] = order[k]
                        row[f"score{k + 1}"] = float(sc[k])
                    rows.append(row)
    out = pd.DataFrame(rows)
    out["top5"] = (out["rank"] <= 5).astype(int)
    out["top10"] = (out["rank"] <= 10).astype(int)
    return out
