"""Demand multipliers and air temperature for the full horizon (all seeded).

The multiplier of node i at step k is

    m_i(k) = D(tod, weekday) * (1 + beta * (T(k) - T_ref)) * (1 + c_i(k)) * (1 + a_i(k))

with D a smooth two-peak daily profile (shifted later and scaled down at weekends),
T the hourly air temperature, a_i an AR(1) process per node, and c_i a spatially
correlated component: a few AR(1) latent series attached to anchor nodes, mixed
with Gaussian weights of the coordinate distance. The product is normalised to a
mean of one over the horizon so base demands keep their meaning.
"""

from __future__ import annotations

import numpy as np

from flowmine.config import DemandConfig, WeatherConfig

DAY = 86400


def ar1(rng: np.random.Generator, n: int, phi: float, sd: float, width: int = 1) -> np.ndarray:
    """Stationary AR(1) series with marginal standard deviation ``sd``, shape (n, width)."""
    eps = rng.normal(0.0, sd * np.sqrt(1.0 - phi**2), size=(n, width))
    out = np.empty((n, width))
    out[0] = rng.normal(0.0, sd, size=width)
    for k in range(1, n):
        out[k] = phi * out[k - 1] + eps[k]
    return out


def daily_profile(hours: np.ndarray, amp: float) -> np.ndarray:
    """Two peaks (07:30 and 20:00) over a night minimum, mean about one."""
    morning = np.exp(-0.5 * ((hours - 7.5) / 1.5) ** 2)
    evening = 0.8 * np.exp(-0.5 * ((hours - 20.0) / 2.0) ** 2)
    night = -0.9 * np.exp(-0.5 * ((hours - 3.5) / 2.0) ** 2)
    shape = morning + evening + night
    return 1.0 + amp * (shape - shape.mean()) / shape.std()


def air_temperature(rng: np.random.Generator, n_hours: int, cfg: WeatherConfig) -> np.ndarray:
    """Hourly air temperature: daily cycle (peak 15:00) plus a slow synoptic AR(1)."""
    h = np.arange(n_hours)
    daily = cfg.daily_amp_c * np.cos(2 * np.pi * ((h % 24) - 15) / 24)
    synoptic = ar1(
        rng, n_hours, cfg.synoptic_phi, cfg.synoptic_sd_c / np.sqrt(1 - cfg.synoptic_phi**2)
    )
    return cfg.mean_c + daily + synoptic[:, 0]


def multipliers(
    rng: np.random.Generator,
    times_s: np.ndarray,
    temp_hourly: np.ndarray,
    xy: np.ndarray,
    length: float,
    cfg: DemandConfig,
) -> np.ndarray:
    """Return an array (n_steps, n_nodes) of demand multipliers."""
    n, m = len(times_s), len(xy)
    hours = (times_s % DAY) / 3600.0
    weekday = (times_s // DAY) % 7
    weekend = weekday >= 5
    prof = np.where(
        weekend,
        cfg.weekend_factor * daily_profile((hours - cfg.weekend_shift_h) % 24, cfg.daily_amp),
        daily_profile(hours, cfg.daily_amp),
    )
    temp = temp_hourly[np.minimum((times_s // 3600).astype(int), len(temp_hourly) - 1)]
    temp_factor = 1.0 + cfg.temp_coeff_per_c * (temp - cfg.temp_ref_c)
    anchors = rng.choice(m, size=min(cfg.spatial_anchors, m), replace=False)
    d2 = ((xy[:, None, :] - xy[None, anchors, :]) ** 2).sum(axis=2)
    w = np.exp(-d2 / (length**2))
    w /= w.sum(axis=1, keepdims=True)
    latent = ar1(rng, n, cfg.ar_phi, cfg.spatial_sd, width=len(anchors))
    spatial = latent @ w.T
    local = ar1(rng, n, cfg.ar_phi, cfg.ar_sd, width=m)
    mult = (prof * temp_factor)[:, None] * (1.0 + spatial) * (1.0 + local)
    mult = np.clip(mult, 0.0, None)
    return mult / mult.mean()
