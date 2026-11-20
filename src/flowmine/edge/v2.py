"""Cloud side of the v0.2 station: fit and calibrate what the station runs.

Three additions to the v0.1 package, all fitted or calibrated in the cloud and
shipped in each sensor's ``meta`` (the model package):

1. ``conf_q``: split conformal thresholds for the forecast-residual test, from the
   calibration week's clean samples (``conformal_tables``);
2. ``cusum``: allowance k and decision interval h of a two-sided CUSUM on the
   standardised residuals, calibrated on the calibration week for a target share of
   clean samples in alarm (``cusum_params``);
3. ``xmodel``: a cross-sensor model per sensor of a multi-sensor station, fitted on
   the training weeks (ridge, or a small MLP when it beats ridge on the calibration
   week), with a conformal threshold on its standardised residual (``fit_xmodels``).

Labels of the calibration week say which samples are clean, as in v0.1. The test
week is never used here.
"""

from __future__ import annotations

import copy
import zlib
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from flowmine.central import nn
from flowmine.config import Config
from flowmine.edge import replay
from flowmine.edge.core import conformal, quality
from flowmine.edge.core.xmodel import TWO_PI
from flowmine.edge.models import SensorModels
from flowmine.testbed.build import Testbed

DAY = 86400
COMPONENTS = ("conformal", "cusum", "cross")


@dataclass
class XFit:
    """One fitted cross-sensor model and how it compared on the calibration week."""

    sensor_id: str
    station: str
    inputs: list[str]
    chosen: str
    rmse_ridge: float
    rmse_mlp: float
    spec: dict[str, Any]
    net: nn.MLP | None


def _input_rows(
    wide: pd.DataFrame, target: str, inputs: list[str], periods: dict[str, int], t0: int, t1: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Target times, target values and raw feature matrix on [t0, t1).

    For each target time t, each input contributes its latest sample strictly before t
    and the sample one period earlier, as ``Recent.at`` gives them on the station.
    """
    y = wide[target]
    times = y.index.to_numpy()
    sel = (times >= t0) & (times < t1) & (times % periods[target] == 0)
    times = times[sel]
    yv = y.to_numpy()[sel]
    cols = []
    for o in inputs:
        p = periods[o]
        s = wide[o]
        last = (times - 1) // p * p
        v = s.reindex(last).to_numpy()
        pv = s.reindex(last - p).to_numpy()
        cols += [v, v - pv, v * v]
    a = TWO_PI * (times % DAY) / DAY
    cols += [np.sin(a), np.cos(a), np.sin(2 * a), np.cos(2 * a)]
    return times, yv, np.column_stack(cols)


def _clean(tb: Testbed, sids: list[str], times: np.ndarray, period: dict[str, int]) -> np.ndarray:
    lab = tb.labels
    ok = np.ones(len(times), dtype=bool)
    for s in sids:
        g = lab[lab.sensor_id == s].set_index("time_s")
        last = times // period[s] * period[s]
        ft = g.fault_type.reindex(last).fillna("").to_numpy()
        ev = g.event_id.reindex(last).fillna(-1).to_numpy()
        ok &= (ft == "") & (ev < 0)
    return ok


def _ridge(z: np.ndarray, yz: np.ndarray, alpha: float) -> np.ndarray:
    a = z.T @ z + alpha * np.eye(z.shape[1])
    return np.linalg.solve(a, z.T @ yz)


def fit_xmodels(tb: Testbed, cfg: Config, hidden: int = 8, epochs: int = 40) -> list[XFit]:
    """Fit a cross-sensor model for every sensor of every multi-sensor station."""
    wide = tb.wide("observed")
    per = {s.sensor_id: s.period_s for s in tb.sensors}
    local = {s.sensor_id: s.local_id for s in tb.sensors}
    tr, ca = tb.periods["train"], tb.periods["calib"]
    out = []
    for st in tb.stations:
        if len(st.sensors) < 2:
            continue
        for target in st.sensors:
            inputs = [s for s in st.sensors if s != target]
            _, y, x = _input_rows(wide, target, inputs, per, *tr)
            ok = np.isfinite(x).all(axis=1) & np.isfinite(y)
            x, y = x[ok], y[ok]
            mu, sd = x.mean(axis=0), x.std(axis=0)
            sd[sd == 0] = 1.0
            ymu, ysd = float(y.mean()), float(y.std()) or 1.0
            z, yz = (x - mu) / sd, (y - ymu) / ysd
            w = _ridge(z, yz, cfg.central.ridge_alpha)
            rng = np.random.default_rng([tb.seed, zlib.crc32(target.encode()) % 997, 81])
            net = nn.init_mlp(rng, z.shape[1], hidden, 1, 300, 0.0, 1.0)
            net = nn.train(net, z, yz, epochs, 0.003, 256, rng)
            tc, yc, xc = _input_rows(wide, target, inputs, per, *ca)
            okc = np.isfinite(xc).all(axis=1) & np.isfinite(yc)
            okc &= _clean(tb, [target, *inputs], tc, per)
            zc = (xc[okc] - mu) / sd
            ref = yc[okc]
            rmse_r = float(np.sqrt(np.mean((ymu + ysd * (zc @ w) - ref) ** 2)))
            rmse_m = float(np.sqrt(np.mean((ymu + ysd * nn.forward(net, zc) - ref) ** 2)))
            use_mlp = rmse_m < rmse_r
            spec: dict[str, Any] = {
                "inputs": [local[o] for o in inputs],
                "mu": [float(v) for v in mu],
                "sd": [float(v) for v in sd],
                "ymu": ymu,
                "ysd": ysd,
                "max_age": 2 * max(per[o] for o in [target, *inputs]),
            }
            if use_mlp:
                spec["kind"] = "mlp"
                spec["layers"] = mlp_layers(net)
                fit = ymu + ysd * nn.forward(net, z)
            else:
                spec["kind"] = "ridge"
                spec["w"] = [float(v) for v in w]
                spec["b"] = 0.0
                fit = ymu + ysd * (z @ w)
            spec["s"] = float(np.std(y - fit)) or 1e-6
            out.append(
                XFit(
                    target,
                    st.station_id,
                    inputs,
                    spec["kind"],
                    rmse_r,
                    rmse_m,
                    spec,
                    net if use_mlp else None,
                )
            )
    return out


def mlp_layers(net: nn.MLP) -> list[list[Any]]:
    """Dense layers in the xmodel spec format (float weights)."""
    return [
        [
            int(w.shape[1]),
            int(w.shape[0]),
            act,
            [float(v) for v in w.ravel()],
            [float(v) for v in b],
        ]
        for w, b, act in ((net.w1, net.b1, 1), (net.w2, net.b2, 0))
    ]


def quantise_layers(layers: list[list[Any]]) -> list[list[Any]]:
    """int8 weights with one scale per tensor: w = scale * round(w / scale)."""
    out = []
    for n_in, n_out, act, w, b in layers:
        a = np.asarray(w)
        scale = float(np.max(np.abs(a))) / 127.0 or 1.0
        q = np.clip(np.round(a / scale), -127, 127).astype(int)
        out.append([n_in, n_out, act, [int(v) for v in q], b, scale])
    return out


def _trace_run(
    tb: Testbed, bundles: dict[str, SensorModels], t0: int, t1: int
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Run every station over [t0, t1) and collect residual and cross-model traces."""
    rows, xrows, recs = [], [], []
    for spec in tb.stations:
        node = replay.make_node(tb, spec, bundles, "validated", "hw")
        by_local = {tb.sensor(s).local_id: s for s in spec.sensors}
        for lid, p in node.core.pipelines.items():
            p.trace = []
            p.sid = by_local[lid]
        node.core.xtrace = []
        replay.run_station(node, tb, t0, t1)
        for p in node.core.pipelines.values():
            rows += [(p.sid, t, z, f) for t, z, f in p.trace]
        xrows += [(by_local[lid], t, sc) for lid, t, sc in node.core.xtrace]
        recs += node.records
    tr = pd.DataFrame(rows, columns=["sensor_id", "time_s", "z", "f"])
    xt = pd.DataFrame(xrows, columns=["sensor_id", "time_s", "xz"])
    rec = pd.DataFrame(recs, columns=["sensor_id", "time_s", "raw", "value", "q", "score"])
    lab = tb.labels[["sensor_id", "time_s", "fault_type", "event_id"]]
    return (
        tr.merge(lab, on=["sensor_id", "time_s"], how="left"),
        xt.merge(lab, on=["sensor_id", "time_s"], how="left"),
        rec,
    )


def is_clean(df: pd.DataFrame) -> pd.Series:
    return (df.fault_type == "") & (df.event_id < 0)


def conformal_tables(trace: pd.DataFrame, alpha: float, buckets: int) -> dict[str, list[float]]:
    """Per sensor: threshold on |z| per time-of-day bucket at level alpha."""
    out = {}
    c = trace[is_clean(trace)]
    for sid, g in c.groupby("sensor_id"):
        b = (g.time_s.to_numpy() % DAY) * buckets // DAY
        z = np.abs(g.z.to_numpy())
        out[str(sid)] = [
            float(conformal.split_quantile(z[b == i].tolist(), alpha)) for i in range(buckets)
        ]
    return out


def cusum_stat(z: np.ndarray, k: float, clip: float = 4.0) -> np.ndarray:
    zc = np.clip(z, -clip, clip)
    sp = sn = 0.0
    out = np.empty(len(zc))
    for i, v in enumerate(zc):
        sp = max(0.0, sp + v - k)
        sn = max(0.0, sn - v - k)
        out[i] = max(sp, sn)
    return out


def cusum_params(
    trace: pd.DataFrame,
    target: float,
    ks: tuple[float, ...] = (0.25, 0.5, 0.75, 1.0),
    faults: tuple[str, ...] = ("drift",),
    col: str = "z",
) -> tuple[dict[str, list[float]], pd.DataFrame]:
    """Per-sensor [k, h]: k shared (best recall of ``faults`` on the calibration
    week), h the (1 - target) quantile of the statistic on clean samples, so that
    about ``target`` of clean samples are in alarm."""
    acc = trace[trace.f == 0] if "f" in trace else trace
    acc = acc.sort_values(["sensor_id", "time_s"]).rename(columns={col: "z"})
    clean = is_clean(acc).to_numpy()
    drift = acc.fault_type.isin(faults).to_numpy()
    sids = acc.sensor_id.to_numpy()
    rows = []
    best: tuple[float, float, dict[str, float]] | None = None
    for k in ks:
        stat = np.concatenate(
            [cusum_stat(g.z.to_numpy(), k) for _, g in acc.groupby("sensor_id", sort=True)]
        )
        hs = {}
        alarm = np.zeros(len(stat), dtype=bool)
        for sid in np.unique(sids):
            m = sids == sid
            cs = stat[m & clean]
            h = float(np.quantile(cs, 1.0 - target)) if len(cs) else float("inf")
            hs[str(sid)] = max(h, 1.0)
            alarm[m] = stat[m] > hs[str(sid)]
        rec = float(alarm[drift].mean()) if drift.any() else 0.0
        far = float(alarm[clean].mean())
        rows.append({"k": k, "drift_recall_calib": rec, "clean_alarm_calib": far})
        if best is None or rec > best[1]:
            best = (k, rec, hs)
    assert best is not None
    k, _, hs = best
    return {sid: [k, h] for sid, h in hs.items()}, pd.DataFrame(rows).assign(chosen=k)


@dataclass
class V2Fit:
    bundles: dict[str, SensorModels]
    xfits: list[XFit]
    trace: pd.DataFrame
    xtrace: pd.DataFrame
    cusum_choice: pd.DataFrame
    conf_trace: pd.DataFrame  # pass-1 residual trace (level shifts left out)


def calibrate(
    tb: Testbed,
    bundles: dict[str, SensorModels],
    cfg: Config,
    xfits: list[XFit] | None = None,
) -> V2Fit:
    """Week-7 calibration of all three additions; returns v0.2 bundles.

    Two passes over the calibration week. Pass 1 runs the v0.1 pipeline and gives
    the conformal residual thresholds (samples later released as level shifts are
    left out: the residual test does not reject them). Pass 2 runs with those
    thresholds installed, so the CUSUM and the cross-sensor thresholds are calibrated
    on the residuals the v0.2 station will actually see.
    """
    e = cfg.edge
    if xfits is None:
        xfits = fit_xmodels(tb, cfg)
    t0, t1 = tb.periods["calib"]
    trace1, _, rec1 = _trace_run(tb, bundles, t0, t1)
    ct = drop_shifts(trace1, rec1)
    conf = conformal_tables(ct, e.conformal_alpha, 1)
    stage = with_conformal(bundles, conf)
    for xf in xfits:
        spec = dict(xf.spec)
        if e.xmodel_int8 and spec["kind"] == "mlp":
            spec = {**spec, "layers": quantise_layers(spec["layers"]), "q8": 1}
        stage[xf.sensor_id].meta["xmodel"] = {**spec, "q": conformal.INF}
    trace, xtrace, _ = _trace_run(tb, stage, t0, t1)
    cus, choice = cusum_params(trace, e.cusum_target)
    xcus, xchoice = cusum_params(
        xtrace,
        e.cross_cusum_target,
        ks=(0.5, 1.0, 1.5, 2.0, 3.0),
        faults=("bias", "drift"),
        col="xz",
    )
    choice = pd.concat([choice.assign(test="forecast"), xchoice.assign(test="cross")])
    xc = xtrace[is_clean(xtrace)]
    out = {}
    for sid, b in stage.items():
        nb = copy.copy(b)
        meta: dict[str, Any] = dict(b.meta)
        if sid in cus:
            meta["cusum"] = cus[sid]
        if "xmodel" in meta:
            sc = xc.loc[xc.sensor_id == sid, "xz"].abs().tolist()
            meta["xmodel"] = {
                **meta["xmodel"],
                "q": conformal.split_quantile(sc, e.cross_alpha),
                "cus": xcus.get(sid),
            }
        nb.meta = meta
        out[sid] = nb
    return V2Fit(out, xfits, trace, xtrace, choice, ct)


def drop_shifts(trace: pd.DataFrame, records: pd.DataFrame) -> pd.DataFrame:
    """Trace rows whose record was not released as a level shift."""
    sh = records.loc[(records.q & quality.SHIFT) != 0, ["sensor_id", "time_s"]]
    m = trace.merge(sh.assign(_shift=True), on=["sensor_id", "time_s"], how="left")
    return m[m._shift.isna()].drop(columns="_shift")


def without(bundles: dict[str, SensorModels], drop: tuple[str, ...]) -> dict[str, SensorModels]:
    """The same v0.2 bundles with some components removed (ablation)."""
    keys = {"conformal": "conf_q", "cusum": "cusum", "cross": "xmodel"}
    out = {}
    for sid, b in bundles.items():
        nb = copy.copy(b)
        nb.meta = {k: v for k, v in b.meta.items() if k not in {keys[d] for d in drop}}
        out[sid] = nb
    return out


def with_conformal(
    bundles: dict[str, SensorModels], tables: dict[str, list[float]]
) -> dict[str, SensorModels]:
    """v0.1 bundles with only the conformal residual thresholds installed."""
    out = {}
    for sid, b in bundles.items():
        nb = copy.copy(b)
        nb.meta = {**b.meta, "conf_q": tables[sid]} if sid in tables else dict(b.meta)
        out[sid] = nb
    return out


def recon_flags(q: pd.Series) -> pd.Series:
    return (q & quality.RECONSTRUCTED) != 0


def quantised(bundles: dict[str, SensorModels]) -> dict[str, SensorModels]:
    """The same bundles with every cross-sensor MLP stored as int8 (per-tensor scale)."""
    out = {}
    for sid, b in bundles.items():
        nb = copy.copy(b)
        meta: dict[str, Any] = dict(b.meta)
        xm = meta.get("xmodel")
        if isinstance(xm, dict) and xm.get("kind") == "mlp" and not xm.get("q8"):
            meta["xmodel"] = {**xm, "layers": quantise_layers(xm["layers"]), "q8": 1}
        nb.meta = meta
        out[sid] = nb
    return out


def layers_forward(layers: list[list[Any]], z: np.ndarray) -> np.ndarray:
    """numpy forward pass of xmodel layers (float or int8 with a scale)."""
    x = z
    for layer in layers:
        n_in, n_out, act, w, b = layer[:5]
        scale = layer[5] if len(layer) > 5 else 1.0
        wm = np.asarray(w, dtype=float).reshape(n_out, n_in) * scale
        x = x @ wm.T + np.asarray(b)
        if act == 1:
            x = np.maximum(x, 0.0)
    return np.asarray(x[:, 0])


def int8_check(tb: Testbed, xfits: list[XFit], bundles: dict[str, SensorModels]) -> pd.DataFrame:
    """RMSE change from storing MLP weights as int8, on clean samples of weeks 7 and 8.

    Cross-sensor MLPs: prediction error of the target. Neural forecasters: one-step
    error of the pressure loggers' forecaster (``edge_mlp``)."""
    wide = tb.wide("observed")
    per = {s.sensor_id: s.period_s for s in tb.sensors}
    rows = []
    for xf in xfits:
        if xf.net is None:
            continue
        spec = xf.spec
        q = quantise_layers(spec["layers"])
        for period in ("calib", "test"):
            times, y, x = _input_rows(wide, xf.sensor_id, xf.inputs, per, *tb.periods[period])
            ok = np.isfinite(x).all(axis=1) & np.isfinite(y)
            ok &= _clean(tb, [xf.sensor_id, *xf.inputs], times, per)
            z = (x[ok] - np.asarray(spec["mu"])) / np.asarray(spec["sd"])
            for name, layers in (("float32", spec["layers"]), ("int8", q)):
                pred = spec["ymu"] + spec["ysd"] * layers_forward(layers, z)
                rows.append(
                    {
                        "model": "cross_mlp",
                        "sensor_id": xf.sensor_id,
                        "period": period,
                        "weights": name,
                        "rmse": float(np.sqrt(np.mean((pred - y[ok]) ** 2))),
                    }
                )
    for s in tb.sensors:
        if s.kind != "pressure" or not s.station.startswith("LG-"):
            continue
        net = bundles[s.sensor_id].net
        ws: list[np.ndarray] = net.weights()
        qws: list[np.ndarray] = []
        for w in ws:
            sc = float(np.max(np.abs(w))) / 127.0 or 1.0
            qws.append(np.clip(np.round(w / sc), -127, 127) * sc if w.ndim == 2 else w)
        series = wide[s.sensor_id]
        for period in ("calib", "test"):
            a, b = tb.periods[period]
            start = a - 86400
            win = series[(series.index >= start) & (series.index < b)]
            vals = win[win.index % s.period_s == 0].to_numpy(dtype=float)
            z = (vals - net.mean) / net.std
            xf_, yf = nn.features(z, net.window, net.s1, net.s2, start // s.period_s)
            for name, wl in (("float32", ws), ("int8", qws)):
                err = (nn.forward(wl, xf_) - yf) * net.std
                rows.append(
                    {
                        "model": "forecast_mlp",
                        "sensor_id": s.sensor_id,
                        "period": period,
                        "weights": name,
                        "rmse": float(np.sqrt(np.mean(err**2))),
                    }
                )
    return pd.DataFrame(rows)
