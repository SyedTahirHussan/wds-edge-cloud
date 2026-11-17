"""Training of the edge neural forecaster (numpy, manual backpropagation, Adam).

The network is ``n_in -> hidden (ReLU) -> 1`` and predicts the change of the z-scored
value from the last slot to the next. Features are built exactly as the edge builds
them (``flowmine.edge.core.mlp``): the last ``window`` values, the value one day
earlier, and time-of-day / day-of-week encodings. ``export`` writes the compact
weight file the station reads.

PyTorch was the planned training library; it could not be installed in this
environment within the time limit set for it, so this module implements the small
network directly (see REPORT/limitations). The pure-Python edge forward pass is
checked against ``forward`` here.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np

from flowmine.edge.core.mlp import HEADER, LAYER, MAGIC

DAY = 86400


@dataclass
class MLP:
    w1: np.ndarray  # (hidden, n_in)
    b1: np.ndarray
    w2: np.ndarray  # (1, hidden)
    b2: np.ndarray
    window: int
    s1: int
    s2: int
    period: int
    mean: float
    std: float

    @property
    def n_params(self) -> int:
        return int(self.w1.size + self.b1.size + self.w2.size + self.b2.size)

    def weights(self) -> list[np.ndarray]:
        return [self.w1, self.b1, self.w2, self.b2]

    def with_weights(
        self, ws: list[np.ndarray], mean: float | None = None, std: float | None = None
    ) -> MLP:
        return MLP(
            ws[0].copy(),
            ws[1].copy(),
            ws[2].copy(),
            ws[3].copy(),
            self.window,
            self.s1,
            self.s2,
            self.period,
            self.mean if mean is None else mean,
            self.std if std is None else std,
        )


def init_mlp(
    rng: np.random.Generator,
    n_in: int,
    hidden: int,
    window: int,
    period: int,
    mean: float,
    std: float,
) -> MLP:
    s1 = DAY // period
    return MLP(
        rng.normal(0, np.sqrt(2.0 / n_in), (hidden, n_in)),
        np.zeros(hidden),
        rng.normal(0, np.sqrt(1.0 / hidden), (1, hidden)) * 0.1,
        np.zeros(1),
        window,
        s1,
        7 * s1,
        period,
        mean,
        std,
    )


def features(z: np.ndarray, window: int, s1: int, s2: int) -> tuple[np.ndarray, np.ndarray]:
    """Rows for every slot k with full history: inputs to predict k+1, target z[k+1]-z[k]."""
    ks = np.arange(max(window - 1, s1 - 1), len(z) - 1)
    lags = np.stack([z[ks - i] for i in range(window)], axis=1)
    day = z[ks + 1 - s1][:, None]
    a = 2 * np.pi * ((ks + 1) % s1) / s1
    b = 2 * np.pi * ((ks + 1) % s2) / s2
    tf = np.stack([np.sin(a), np.cos(a), np.sin(b), np.cos(b)], axis=1)
    x = np.concatenate([lags, day, tf], axis=1)
    y = z[ks + 1] - z[ks]
    ok = np.isfinite(x).all(axis=1) & np.isfinite(y)
    return x[ok], y[ok]


def forward(m: MLP | list[np.ndarray], x: np.ndarray) -> np.ndarray:
    w1, b1, w2, b2 = m.weights() if isinstance(m, MLP) else m
    h = np.maximum(x @ w1.T + b1, 0.0)
    return (h @ w2.T + b2)[:, 0]


def train(
    m: MLP,
    x: np.ndarray,
    y: np.ndarray,
    epochs: int,
    lr: float,
    batch: int,
    rng: np.random.Generator,
) -> MLP:
    """Mini-batch Adam on mean squared error; returns a new MLP."""
    ws = [w.copy() for w in m.weights()]
    mom = [np.zeros_like(w) for w in ws]
    vel = [np.zeros_like(w) for w in ws]
    b1c, b2c, eps = 0.9, 0.999, 1e-8
    t = 0
    for _ in range(epochs):
        order = rng.permutation(len(x))
        for s in range(0, len(x), batch):
            idx = order[s : s + batch]
            xb, yb = x[idx], y[idx]
            pre = xb @ ws[0].T + ws[1]
            h = np.maximum(pre, 0.0)
            out = (h @ ws[2].T + ws[3])[:, 0]
            g_out = (2.0 / len(idx)) * (out - yb)[:, None]
            g_w2 = g_out.T @ h
            g_b2 = g_out.sum(axis=0)
            g_h = g_out @ ws[2]
            g_pre = g_h * (pre > 0)
            grads = [g_pre.T @ xb, g_pre.sum(axis=0), g_w2, g_b2]
            t += 1
            for i, g in enumerate(grads):
                mom[i] = b1c * mom[i] + (1 - b1c) * g
                vel[i] = b2c * vel[i] + (1 - b2c) * g * g
                mh = mom[i] / (1 - b1c**t)
                vh = vel[i] / (1 - b2c**t)
                ws[i] -= lr * mh / (np.sqrt(vh) + eps)
    return m.with_weights(ws)


def export(m: MLP) -> bytes:
    """Serialise to the edge weight format (float32)."""
    hidden, n_in = m.w1.shape
    buf = bytearray(struct.pack(HEADER, MAGIC, 2, m.window, m.s1, m.s2, m.period, m.mean, m.std))
    for w, b, act in ((m.w1, m.b1, 1), (m.w2, m.b2, 0)):
        buf += struct.pack(LAYER, w.shape[1], w.shape[0], act)
        buf += w.astype("<f4").tobytes()
        buf += b.astype("<f4").tobytes()
    assert n_in == m.w1.shape[1] and hidden == m.w2.shape[1]
    return bytes(buf)


def one_step_mse(m: MLP, z: np.ndarray) -> float:
    x, y = features(z, m.window, m.s1, m.s2)
    return float(np.mean((forward(m, x) - y) ** 2)) if len(x) else float("nan")
