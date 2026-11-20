"""Typed experiment configuration loaded from YAML.

``configs/default.yaml`` holds every parameter; other files (``smoke.yaml``) list only
the keys they change and name their base with ``extends``. Unknown keys are an error
so that a typo cannot silently fall back to a default.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, get_type_hints

import yaml


@dataclass(frozen=True)
class SensorClassConfig:
    """Instrument model and edge test parameters for one sensor class (physical units)."""

    period_s: int
    noise_sd: float
    noise_rel: float
    resolution: float
    lo: float
    hi: float
    max_rate_per_s: float
    flat_window: int
    rest_band: float
    epsilon: float
    min_scale: float


@dataclass(frozen=True)
class NetworkConfig:
    inp: str | None = None
    start: str = "2025-06-02T00:00:00+00:00"
    train_days: int = 42
    calib_days: int = 7
    test_days: int = 7
    hydraulic_step_s: int = 300


@dataclass(frozen=True)
class DemandConfig:
    daily_amp: float = 0.35
    weekend_factor: float = 0.92
    weekend_shift_h: float = 1.5
    temp_coeff_per_c: float = 0.015
    temp_ref_c: float = 22.0
    ar_phi: float = 0.97
    ar_sd: float = 0.02
    spatial_sd: float = 0.04
    spatial_length_frac: float = 0.3
    spatial_anchors: int = 4


@dataclass(frozen=True)
class WeatherConfig:
    mean_c: float = 22.0
    daily_amp_c: float = 5.0
    synoptic_phi: float = 0.995
    synoptic_sd_c: float = 0.35


@dataclass(frozen=True)
class QualityConfig:
    bulk_coeff_per_day: float = -0.5
    wall_coeff_m_per_day: float = -0.05
    source_mg_l: float = 1.0
    tank_initial_mg_l: float = 0.6


@dataclass(frozen=True)
class SensorLayoutConfig:
    n_pressure: int = 12
    n_chlorine: int = 5


@dataclass(frozen=True)
class FaultConfig:
    """Number of fault events of each type per labelled week (calibration and test)."""

    gap_short: int = 6
    gap_long: int = 3
    stuck: int = 3
    spike: int = 8
    drift: int = 3
    bias: int = 3
    noise_burst: int = 3
    gap_short_samples: tuple[int, int] = (1, 6)
    gap_long_hours: tuple[float, float] = (2.0, 24.0)
    stuck_hours: tuple[float, float] = (2.0, 8.0)
    # Magnitudes are fractions of the sensor's 5-95 % range over the training days.
    spike_scale: tuple[float, float] = (0.3, 0.8)
    drift_days: tuple[float, float] = (1.0, 3.0)
    drift_scale: tuple[float, float] = (0.2, 0.5)
    bias_scale: tuple[float, float] = (0.15, 0.35)
    bias_hours: tuple[float, float] = (4.0, 24.0)
    noise_hours: tuple[float, float] = (1.0, 4.0)
    noise_scale: tuple[float, float] = (0.05, 0.15)


@dataclass(frozen=True)
class EventConfig:
    """Process events: real hydraulic changes that are not sensor faults."""

    leak_area_m2: float = 0.0015
    leak_discharge_coeff: float = 0.75
    leak_ramp_h: float = 6.0
    leaks_calib: int = 0
    leaks_test: int = 1
    surge_l_s: tuple[float, float] = (15.0, 30.0)
    surge_hours: tuple[float, float] = (2.0, 5.0)
    surges_calib: int = 2
    surges_test: int = 3
    operations_calib: int = 2
    operations_test: int = 2
    operation_hours: tuple[float, float] = (2.0, 4.0)


@dataclass(frozen=True)
class LabConfig:
    noise_sd: float = 0.01
    resolution: float = 0.01
    delay_h: tuple[float, float] = (24.0, 48.0)
    hour_range: tuple[float, float] = (8.0, 14.0)


@dataclass(frozen=True)
class EdgeConfig:
    residual_k: float = 4.0
    calib_false_alarm: float = 0.005
    residual_lambda: float = 0.01
    shift_after: int = 2
    max_recon_h: float = 24.0
    hw_grid: tuple[float, ...] = (0.02, 0.05, 0.1, 0.2)
    hw_phi_grid: tuple[float, ...] = (0.0, 0.5, 0.8, 0.95)
    nn_window: int = 12
    nn_hidden: int = 32
    nn_epochs: int = 30
    nn_lr: float = 0.003
    nn_batch: int = 256
    heartbeat_s: int = 3600
    summary_s: int = 900
    max_payload: int = 51
    retention_days: float = 14.0
    tank_tol_m: float = 0.1
    # v0.2 station (fixed before any test-week run; see docs/methods.md)
    conformal_alpha: float = 0.005
    cross_alpha: float = 0.002
    cusum_target: float = 0.002
    cross_cusum_target: float = 0.002
    xmodel_int8: bool = True  # kept: week-7 RMSE change within the seed spread (E8d)


@dataclass(frozen=True)
class CentralConfig:
    grid_s: int = 300
    pca_variance: float = 0.99
    ridge_alpha: float = 1.0
    ridge_lags: int = 2
    alarm_quantile: float = 0.995
    triage_window: int = 6
    routine_step_frac: float = 0.3
    cusum_k: float = 0.5
    cusum_h: float = 5.0
    suppress_h: float = 6.0
    twin_level_tol_m: float = 0.2  # chosen by a sweep on the calibration week, seed 1


@dataclass(frozen=True)
class ExperimentConfig:
    gap_lengths_min: tuple[int, ...] = (15, 60, 180, 360, 720, 1440)
    gaps_per_length: int = 12
    epsilons: tuple[float, ...] = (0.25, 0.5, 1.0, 2.0, 4.0)
    outage_hours: tuple[float, ...] = (6.0, 24.0)
    fed_rounds: int = 10
    fed_local_epochs: int = 3
    cold_start_days: int = 3
    unmonitored_nodes: int = 6
    backfill_frames_per_hour: int = 30
    outage_start_h: float = 36.0
    day_ahead_origins: int = 7
    workers: int = 5


@dataclass(frozen=True)
class Config:
    seeds: tuple[int, ...] = (1, 2, 3, 4, 5)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    demand: DemandConfig = field(default_factory=DemandConfig)
    weather: WeatherConfig = field(default_factory=WeatherConfig)
    quality: QualityConfig = field(default_factory=QualityConfig)
    layout: SensorLayoutConfig = field(default_factory=SensorLayoutConfig)
    sensors: dict[str, SensorClassConfig] = field(default_factory=dict)
    faults: FaultConfig = field(default_factory=FaultConfig)
    events: EventConfig = field(default_factory=EventConfig)
    lab: LabConfig = field(default_factory=LabConfig)
    edge: EdgeConfig = field(default_factory=EdgeConfig)
    central: CentralConfig = field(default_factory=CentralConfig)
    experiments: ExperimentConfig = field(default_factory=ExperimentConfig)
    name: str = "default"

    @property
    def total_days(self) -> int:
        n = self.network
        return n.train_days + n.calib_days + n.test_days

    def digest(self) -> str:
        """Stable hash of the full configuration (recorded in every manifest)."""
        blob = json.dumps(dataclasses.asdict(self), sort_keys=True, default=list)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _build[T](cls: type[T], data: Any) -> T:
    hints = get_type_hints(cls)
    known = {f.name for f in fields(cls)}  # type: ignore[arg-type]
    extra = set(data) - known
    if extra:
        raise ValueError(f"unknown keys for {cls.__name__}: {sorted(extra)}")
    kwargs: dict[str, Any] = {}
    for name, value in data.items():
        kwargs[name] = _coerce(hints[name], value)
    return cls(**kwargs)


def _coerce(tp: Any, value: Any) -> Any:
    origin = getattr(tp, "__origin__", None)
    if is_dataclass(tp) and isinstance(tp, type):
        return _build(tp, value)
    if origin is tuple:
        return tuple(value)
    if origin is dict:
        _, vt = tp.__args__
        return {k: _coerce(vt, v) for k, v in value.items()}
    return value


def _merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k != "sensors":
            out[k] = _merge(out[k], v)
        elif isinstance(v, dict) and k == "sensors":
            merged = dict(out.get(k, {}))
            for cls_name, params in v.items():
                merged[cls_name] = {**merged.get(cls_name, {}), **params}
            out[k] = merged
        else:
            out[k] = v
    return out


def _read(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(path.read_text()) or {}
    parent = data.pop("extends", None)
    if parent is not None:
        data = _merge(_read(path.parent / parent), data)
    return data


def load_config(path: str | Path) -> Config:
    p = Path(path)
    data = _read(p)
    data.setdefault("name", p.stem)
    cfg = _build(Config, data)
    missing = {"pressure", "flow", "level", "chlorine"} - set(cfg.sensors)
    if missing:
        raise ValueError(f"sensor classes missing from config: {sorted(missing)}")
    return cfg
