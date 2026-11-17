"""Sensor fault injection with per-sample labels.

Each injector modifies only the samples of its interval and returns nothing; the
scheduler records the interval, so the labels are exactly the modified samples.
Magnitudes are fractions of the sensor's 5-95 % range over the training days, so
the same configuration gives comparable faults on pressures, flows and chlorine.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from flowmine.config import FaultConfig

FAULT_TYPES = ("gap_short", "gap_long", "stuck", "spike", "drift", "bias", "noise_burst")
GAPS = ("gap_short", "gap_long")


@dataclass(frozen=True)
class FaultEvent:
    fault_id: int
    sensor_id: str
    fault_type: str
    start_idx: int
    n: int
    magnitude: float
    period: str  # calib | test

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def inject(v: np.ndarray, ev: FaultEvent, rng: np.random.Generator) -> None:
    """Apply one fault in place to the observed series ``v`` (NaN = no sample)."""
    sl = slice(ev.start_idx, ev.start_idx + ev.n)
    if ev.fault_type in GAPS:
        v[sl] = np.nan
    elif ev.fault_type == "stuck":
        prev = v[ev.start_idx - 1] if ev.start_idx > 0 else v[ev.start_idx]
        if np.isnan(prev):
            prev = np.nanmean(v[max(0, ev.start_idx - 12) : ev.start_idx + 1])
        v[sl] = prev
    elif ev.fault_type == "spike":
        v[sl] += ev.magnitude
    elif ev.fault_type == "drift":
        v[sl] += ev.magnitude * np.linspace(1.0 / ev.n, 1.0, ev.n)
    elif ev.fault_type == "bias":
        v[sl] += ev.magnitude
    elif ev.fault_type == "noise_burst":
        v[sl] += rng.normal(0.0, ev.magnitude, size=ev.n)
    else:
        raise ValueError(ev.fault_type)


def _duration(ft: str, cfg: FaultConfig, period_s: int, rng: np.random.Generator) -> int:
    per_h = 3600.0 / period_s
    if ft == "gap_short":
        lo, hi = cfg.gap_short_samples
        return int(rng.integers(lo, hi + 1))
    if ft == "spike":
        return 1
    hours = {
        "gap_long": cfg.gap_long_hours,
        "stuck": cfg.stuck_hours,
        "bias": cfg.bias_hours,
        "noise_burst": cfg.noise_hours,
        "drift": (cfg.drift_days[0] * 24, cfg.drift_days[1] * 24),
    }[ft]
    return max(2, round(rng.uniform(*hours) * per_h))


def _magnitude(ft: str, cfg: FaultConfig, span: float, rng: np.random.Generator) -> float:
    sign = 1.0 if rng.random() < 0.5 else -1.0
    rng_by_type = {
        "spike": cfg.spike_scale,
        "drift": cfg.drift_scale,
        "bias": cfg.bias_scale,
    }
    if ft in rng_by_type:
        return sign * rng.uniform(*rng_by_type[ft]) * span
    if ft == "noise_burst":
        return rng.uniform(*cfg.noise_scale) * span
    return 0.0


def schedule(
    rng: np.random.Generator,
    sensors: list[dict[str, object]],
    spans: dict[str, float],
    periods: dict[str, tuple[int, int]],
    cfg: FaultConfig,
    weeks: dict[str, float],
    margin: int = 12,
) -> list[FaultEvent]:
    """Draw non-overlapping fault intervals for each labelled period.

    ``periods`` maps period name to ``(start_s, end_s)``; ``weeks`` maps it to its
    length in weeks (counts in the config are per week, rounded up). Drift faults
    alternate between chlorine analysers and other sensors so the laboratory
    comparison has drifts to find.
    """
    events: list[FaultEvent] = []
    busy: dict[str, list[tuple[int, int]]] = {str(s["sensor_id"]): [] for s in sensors}
    chlorine = [s for s in sensors if s["kind"] == "chlorine"]
    fid = 0
    for pname, (t0, t1) in periods.items():
        for ft in FAULT_TYPES:
            count = int(np.ceil(getattr(cfg, ft) * weeks[pname]))
            for j in range(count):
                pool = chlorine if (ft == "drift" and j % 2 == 0 and chlorine) else sensors
                for _attempt in range(200):
                    s = pool[int(rng.integers(len(pool)))]
                    sid = str(s["sensor_id"])
                    per = int(s["period_s"])  # type: ignore[call-overload]
                    n = _duration(ft, cfg, per, rng)
                    lo, hi = t0 // per + 1, t1 // per - n - 1
                    if hi <= lo:
                        continue
                    i0 = int(rng.integers(lo, hi))
                    if any(i0 < b + margin and a < i0 + n + margin for a, b in busy[sid]):
                        continue
                    busy[sid].append((i0, i0 + n))
                    events.append(
                        FaultEvent(fid, sid, ft, i0, n, _magnitude(ft, cfg, spans[sid], rng), pname)
                    )
                    fid += 1
                    break
    return events
