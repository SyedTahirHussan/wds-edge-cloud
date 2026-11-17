"""Three training paradigms for the edge neural forecaster, with bytes moved.

* local: each sensor's model is trained on its own station; nothing travels.
* centralised: stations upload their raw training samples (binary frames, 6 bytes a
  sample plus headers), the cloud trains one model per sensor class on the pooled,
  per-sensor z-scored data and sends the weights back.
* federated averaging (McMahan et al., 2017): the cloud sends global weights, each
  station trains ``local_epochs`` on its own data, the cloud averages the returned
  weights in proportion to sample counts; ``rounds`` times. Optionally each station
  fine-tunes the final global model on its own data (one epoch).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from flowmine.central import nn
from flowmine.config import Config
from flowmine.edge.core import codec
from flowmine.edge.core.mlp import ZCLIP

FRAME_RECORDS = 32


@dataclass
class Trained:
    nets: dict[str, nn.MLP]
    bytes_up: int
    bytes_down: int


def _data(
    series: dict[str, np.ndarray], cfg: Config, period: int, slot0: dict[str, int] | None
) -> dict[str, tuple[np.ndarray, np.ndarray, float, float]]:
    out = {}
    w = cfg.edge.nn_window
    s1 = 86400 // period
    for sid, y in series.items():
        mean, std = float(np.nanmean(y)), float(np.nanstd(y)) or 1.0
        k0 = (slot0 or {}).get(sid, 0)
        x, t = nn.features((y - mean) / std, w, s1, 7 * s1, k0)
        out[sid] = (x, t, mean, std)
    return out


def weight_bytes(m: nn.MLP) -> int:
    return len(nn.export(m))


def raw_upload_bytes(n_samples: int) -> int:
    frames = -(-n_samples // FRAME_RECORDS)
    return n_samples * codec.READING_SIZE + frames * codec.HEADER_SIZE


def local(
    series: dict[str, np.ndarray],
    cfg: Config,
    period: int,
    seed: int,
    slot0: dict[str, int] | None = None,
) -> Trained:
    data = _data(series, cfg, period, slot0)
    nets = {}
    e = cfg.edge
    for i, (sid, (x, t, mean, std)) in enumerate(sorted(data.items())):
        rng = np.random.default_rng([seed, 11, i])
        m = nn.init_mlp(rng, x.shape[1], e.nn_hidden, e.nn_window, period, mean, std)
        nets[sid] = nn.train(m, x, t, e.nn_epochs, e.nn_lr, e.nn_batch, rng)
    return Trained(nets, 0, 0)


def centralised(
    series: dict[str, np.ndarray],
    cfg: Config,
    period: int,
    seed: int,
    slot0: dict[str, int] | None = None,
) -> Trained:
    data = _data(series, cfg, period, slot0)
    e = cfg.edge
    rng = np.random.default_rng([seed, 12])
    x = np.concatenate([d[0] for d in data.values()])
    t = np.concatenate([d[1] for d in data.values()])
    g = nn.init_mlp(rng, x.shape[1], e.nn_hidden, e.nn_window, period, 0.0, 1.0)
    g = nn.train(g, x, t, e.nn_epochs, e.nn_lr, e.nn_batch, rng)
    nets = {sid: g.with_weights(g.weights(), d[2], d[3]) for sid, d in data.items()}
    up = sum(raw_upload_bytes(int(np.isfinite(y).sum())) for y in series.values())
    return Trained(nets, up, weight_bytes(g) * len(series))


def federated(
    series: dict[str, np.ndarray],
    cfg: Config,
    period: int,
    seed: int,
    rounds: int,
    local_epochs: int,
    fine_tune: bool,
    slot0: dict[str, int] | None = None,
) -> Trained:
    data = _data(series, cfg, period, slot0)
    e = cfg.edge
    rng = np.random.default_rng([seed, 13])
    n_in = next(iter(data.values()))[0].shape[1]
    g = nn.init_mlp(rng, n_in, e.nn_hidden, e.nn_window, period, 0.0, 1.0)
    wb = weight_bytes(g)
    up = down = 0
    for _ in range(rounds):
        acc = [np.zeros_like(w) for w in g.weights()]
        total = 0
        for i, (_sid, (x, t, _m, _s)) in enumerate(sorted(data.items())):
            lr = np.random.default_rng([seed, 14, i, total])
            local_m = nn.train(g, x, t, local_epochs, e.nn_lr, e.nn_batch, lr)
            for a, w in zip(acc, local_m.weights(), strict=True):
                a += len(x) * w
            total += len(x)
            down += wb
            up += wb
        g = g.with_weights([a / total for a in acc])
    nets = {}
    for i, (sid, (x, t, mean, std)) in enumerate(sorted(data.items())):
        m = g.with_weights(g.weights(), mean, std)
        if fine_tune:
            m = nn.train(m, x, t, 1, e.nn_lr, e.nn_batch, np.random.default_rng([seed, 15, i]))
        nets[sid] = m
    return Trained(nets, up, down)


def day_ahead_rmse(
    m: nn.MLP, inputs: np.ndarray, truth: np.ndarray, origins: list[int], slot0: int = 0
) -> float:
    """Recursive 24 h forecast from each origin index (numpy forward, same maths as the edge).

    ``inputs`` (observed, gaps filled) feed the history; errors are against ``truth``.
    ``slot0`` is the absolute slot of index 0, for the phase features.
    """
    z = (inputs - m.mean) / m.std
    zt = (truth - m.mean) / m.std
    s1 = m.s1
    errs = []
    for k0 in origins:
        hist = list(z[: k0 + 1])
        for _ in range(s1):
            k = len(hist) - 1
            lags = [hist[k - i] for i in range(m.window)]
            a = 2 * np.pi * ((slot0 + k + 1) % s1) / s1
            b = 2 * np.pi * ((slot0 + k + 1) % m.s2) / m.s2
            feat = np.array([*lags, hist[k + 1 - s1], np.sin(a), np.cos(a), np.sin(b), np.cos(b)])
            nxt = hist[k] + float(nn.forward(m, feat[None, :])[0])
            hist.append(min(max(nxt, -ZCLIP), ZCLIP))
        pred = np.array(hist[k0 + 1 : k0 + 1 + s1])
        target = zt[k0 + 1 : k0 + 1 + s1]
        ok = np.isfinite(target)
        errs.append(np.mean((pred[ok] - target[ok]) ** 2))
    return float(np.sqrt(np.mean(errs)) * m.std)
