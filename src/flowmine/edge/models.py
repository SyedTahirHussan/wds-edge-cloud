"""Per-sensor model bundle: what the cloud fits on training data and ships to a station.

A bundle holds the pipeline parameters (bounds, thresholds), the Holt-Winters state at
the end of training, and the neural forecaster's weight file and input history. The
bundle is built only from observed training data, which is what the cloud holds.
"""

from __future__ import annotations

import copy
import zlib
from dataclasses import dataclass, field

import numpy as np

from flowmine.central import hw_fit, nn
from flowmine.config import Config
from flowmine.edge.core.holtwinters import HoltWinters
from flowmine.edge.core.mlp import MLPForecaster
from flowmine.edge.core.snaive import SeasonalNaive
from flowmine.testbed.build import Testbed
from flowmine.testbed.sensors import Sensor

DAY = 86400


def train_series(tb: Testbed, sid: str, end_s: int | None = None) -> np.ndarray:
    """Observed values of one sensor from time 0 up to ``end_s`` (default: end of training)."""
    end = tb.periods["train"][1] if end_s is None else end_s
    obs = tb.observed
    v = obs.loc[(obs.sensor_id == sid) & (obs.time_s < end), "value"]
    return v.to_numpy(dtype=float)


def pipeline_meta(s: Sensor, cfg: Config, init_scale: float) -> dict[str, object]:
    c = cfg.sensors[s.kind]
    e = cfg.edge
    return {
        "period": s.period_s,
        "t0": 0,
        "lo": s.lo,
        "hi": s.hi,
        "max_rate": c.max_rate_per_s,
        "flat_window": c.flat_window,
        "flat_tol": s.resolution,
        "rest_band": c.rest_band,
        "k": e.residual_k,
        "lam": e.residual_lambda,
        "init_scale": max(init_scale, c.min_scale),
        "min_scale": c.min_scale,
        "shift_after": e.shift_after,
        "max_recon": int(e.max_recon_h * 3600 // s.period_s),
    }


def mlp_template(
    rng: np.random.Generator, cfg: Config, period: int, mean: float, std: float
) -> nn.MLP:
    w = cfg.edge.nn_window
    return nn.init_mlp(rng, w + 1 + 4, cfg.edge.nn_hidden, w, period, mean, std)


def zscore(y: np.ndarray) -> tuple[np.ndarray, float, float]:
    mean = float(np.nanmean(y))
    std = float(np.nanstd(y)) or 1.0
    return (y - mean) / std, mean, std


def train_local_mlp(y: np.ndarray, period: int, cfg: Config, seed: int) -> nn.MLP:
    rng = np.random.default_rng(seed)
    z, mean, std = zscore(y)
    m = mlp_template(rng, cfg, period, mean, std)
    x, t = nn.features(z, m.window, m.s1, m.s2)
    e = cfg.edge
    return nn.train(m, x, t, e.nn_epochs, e.nn_lr, e.nn_batch, rng)


@dataclass
class SensorModels:
    sensor_id: str
    meta: dict[str, object]
    hw: HoltWinters
    hw_params: hw_fit.HWParams
    net: nn.MLP
    mlp_hist: list[float]
    last_slot: int
    history: np.ndarray = field(repr=False)

    @property
    def mlp_bytes(self) -> bytes:
        return nn.export(self.net)

    @property
    def mlp_params(self) -> int:
        return self.net.n_params

    def hw_copy(self) -> HoltWinters:
        return copy.deepcopy(self.hw)

    def mlp(self) -> MLPForecaster:
        m = MLPForecaster(self.mlp_bytes, t0=0)
        m.load({"t0": 0, "k": self.last_slot, "hist": self.mlp_hist})
        return m

    def snaive(self) -> SeasonalNaive:
        s2 = self.hw.s2
        m = SeasonalNaive(self.hw.period, s2, t0=0)
        start = self.last_slot - s2 + 1
        for i, v in enumerate(self.history[-s2:]):
            m.update(float(v) if np.isfinite(v) else None, (start + i) * self.hw.period)
        return m

    def forecaster(self, kind: str) -> HoltWinters | MLPForecaster | SeasonalNaive:
        return {"hw": self.hw_copy, "mlp": self.mlp, "snaive": self.snaive}[kind]()

    def with_net(self, net: nn.MLP) -> SensorModels:
        """Same bundle with another network (pooled or federated training)."""
        out = copy.copy(self)
        out.net = net.with_weights(net.weights(), self.net.mean, self.net.std)
        return out


def fit_sensor(
    tb: Testbed, sid: str, cfg: Config, end_s: int | None = None, net: nn.MLP | None = None
) -> SensorModels:
    s = tb.sensor(sid)
    y = train_series(tb, sid, end_s)
    params, hw = hw_fit.fit(y, s.period_s, cfg.edge.hw_grid, cfg.edge.hw_phi_grid)
    scale = hw_fit.residual_scale(y, params, s.period_s)
    if net is None:
        net = train_local_mlp(y, s.period_s, cfg, tb.seed * 1000 + zlib.crc32(sid.encode()) % 997)
    z = (y - net.mean) / net.std
    s1 = DAY // s.period_s
    last = len(y) - 1
    hist = [0.0] * s1
    for k in range(max(0, len(y) - s1), len(y)):
        hist[k % s1] = float(z[k]) if np.isfinite(z[k]) else 0.0
    return SensorModels(sid, pipeline_meta(s, cfg, scale), hw, params, net, hist, last, y)
