"""Leak localisation from the twin's pressure residuals with a sensitivity matrix.

The method follows Pérez et al. (2011, README R1). For each candidate junction j a
nominal leak of ``q`` L/s is added to the twin's own network model (base demands
times one multiplier, as in the bridge), and the same total is removed in proportion
to the base demands, because the twin closes the water balance with the measured
inflows: a leak raises the twin's total demand as well as the measured one, so only
the *distribution* of the extra outflow shows in the residual. The sensitivity is

    S[h, i, j] = (p_i(leak at j) - p_i(no leak)) / q        (m per L/s)

at monitored node i and hour of day h, from a 48-hour extended-period run (the second
day is kept). During a leak the residual vector r (measured minus simulated pressure,
averaged over a window) is compared with each column of S averaged over the window's
hours of day; candidates are ranked by the Pearson correlation of r and S[:, j]
(primary) or by the cosine of the angle between them.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

import networkx as nx
import numpy as np
import wntr

from flowmine.config import Config
from flowmine.testbed import network, simulate

DAY = 86400
STEPS_PER_DAY = DAY // 300


@dataclass
class Sensitivity:
    candidates: list[str]  # junction ids
    monitored: list[str]  # node ids of the pressure sensors
    s: np.ndarray  # (24, n_monitored, n_candidates), m per L/s

    def at_hours(self, hours: np.ndarray) -> np.ndarray:
        """Sensitivity averaged over the given hours of day, shape (n_monitored, n_cand)."""
        h = np.asarray(hours, dtype=int) % 24
        return np.asarray(self.s[h].mean(axis=0))


def twin_network(cfg: Config, duration_s: int) -> tuple[wntr.network.WaterNetworkModel, dict]:
    """The bridge's network: daily controls, one demand entry per junction, no quality."""
    wn = network.load_network(cfg.network.inp)
    network.convert_time_controls(wn)
    n_steps = duration_s // cfg.network.hydraulic_step_s + 1
    base = simulate.assign_demands(wn, np.ones((n_steps, len(wn.junction_name_list))))
    network.configure(wn, cfg, duration_s)
    wn.options.quality.parameter = "NONE"
    return wn, base


def daily_multiplier(total_l_s: np.ndarray, times: np.ndarray, base_total_l_s: float) -> np.ndarray:
    """Mean demand multiplier by 5-minute step of the day (288 values)."""
    slot = (np.asarray(times) % DAY) // 300
    out = np.array([np.mean(total_l_s[slot == k]) for k in range(STEPS_PER_DAY)])
    return out / base_total_l_s


def compute(
    cfg: Config, monitored: list[str], mult: np.ndarray, q_l_s: float = 10.0
) -> Sensitivity:
    """Run the no-leak case and one leak case per candidate junction (48 h each)."""
    duration = 2 * DAY
    wn, base = twin_network(cfg, duration)
    for j in wn.junction_name_list:
        if f"m_{j}" in wn.pattern_name_list:
            wn.get_pattern(f"m_{j}").multipliers = list(np.tile(mult, 2))
    candidates = sorted(wn.junction_name_list)
    total = sum(base.values())
    wn.add_pattern("const", [1.0])
    keep = np.arange(DAY, 2 * DAY, 3600)

    def run() -> np.ndarray:
        res = simulate.run(wn)
        p = res.node["pressure"][monitored]
        return p.loc[keep].to_numpy(dtype=float)  # (24, n_monitored)

    p0 = run()
    # The same total is taken away in proportion to the base demands.
    for j, b in base.items():
        if b > 0:
            wn.get_node(j).add_demand(-q_l_s / 1000.0 * b / total, "const")
    s = np.zeros((24, len(monitored), len(candidates)))
    for k, j in enumerate(candidates):
        node = wn.get_node(j)
        node.add_demand(q_l_s / 1000.0, "const")
        s[:, :, k] = (run() - p0) / q_l_s
        del node.demand_timeseries_list[-1]
    return Sensitivity(candidates, list(monitored), s)


def cache_key(cfg: Config, monitored: list[str], mult: np.ndarray, q_l_s: float) -> str:
    blob = json.dumps(
        {
            "inp": cfg.network.inp or "Net3",
            "step": cfg.network.hydraulic_step_s,
            "monitored": monitored,
            "mult": [round(float(x), 9) for x in mult],
            "q": q_l_s,
        },
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def ensure(
    cfg: Config, monitored: list[str], mult: np.ndarray, root: Path, q_l_s: float = 10.0
) -> Sensitivity:
    """Load the matrix from ``root`` or compute and cache it (keyed by its inputs)."""
    path = root / f"sensitivity-{cache_key(cfg, monitored, mult, q_l_s)}.npz"
    if path.exists():
        z = np.load(path, allow_pickle=False)
        return Sensitivity(
            [str(c) for c in z["candidates"]], [str(m) for m in z["monitored"]], z["s"]
        )
    sens = compute(cfg, monitored, mult, q_l_s)
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=root, suffix=".npz", delete=False) as fh:
        np.savez(
            fh, candidates=np.array(sens.candidates), monitored=np.array(sens.monitored), s=sens.s
        )
    Path(fh.name).replace(path)
    return sens


def scores(sens: np.ndarray, r: np.ndarray, method: str = "correlation") -> np.ndarray:
    """Similarity of residual ``r`` (n_monitored) with each column of ``sens``.

    Sensors with a missing residual are left out of the comparison.
    """
    ok = np.isfinite(r)
    x = r[ok]
    a = sens[ok]
    if method == "correlation":
        x = x - x.mean()
        a = a - a.mean(axis=0, keepdims=True)
    elif method != "angle":
        raise ValueError(f"unknown method {method}")
    den = np.linalg.norm(a, axis=0) * np.linalg.norm(x)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = (a.T @ x) / den
    return np.where(np.isfinite(out), out, -np.inf)


def ranking(
    sens: Sensitivity, r: np.ndarray, hours: np.ndarray, method: str = "correlation"
) -> tuple[list[str], np.ndarray]:
    """Candidates ordered from most to least likely, with their scores."""
    sc = scores(sens.at_hours(hours), r, method)
    order = np.lexsort((np.array(sens.candidates), -sc))
    return [sens.candidates[k] for k in order], sc[order]


def rank_of(order: list[str], true_nodes: list[str]) -> int:
    """1-based rank of the best-placed true node."""
    return 1 + min(order.index(n) for n in true_nodes if n in order)


class LeakWatcher:
    """Live use: rank candidates from the mean residual of the last ``window_s``.

    Every ``every_s`` of model time, when the norm of the window's mean residual vector
    reaches ``alarm_norm_m``, the ranking is returned (the caller publishes it). The
    alarm rule is a demo setting; E9 evaluates the localisation, not this detector.
    """

    def __init__(
        self,
        sens: Sensitivity,
        sensor_node: dict[str, str],
        window_s: int = 6 * 3600,
        every_s: int = 3600,
        alarm_norm_m: float = 0.5,
        method: str = "angle",
    ) -> None:
        self.sens = sens
        node_sensor = {n: s for s, n in sensor_node.items()}
        self.ids = [node_sensor.get(n, "") for n in sens.monitored]
        self.window_s = window_s
        self.every_s = every_s
        self.alarm = alarm_norm_m
        self.method = method
        self.rows: list[tuple[int, np.ndarray]] = []

    def add(self, t: int, residual: dict[str, float]) -> tuple[list[str], list[float]] | None:
        v = np.array([residual.get(s, np.nan) for s in self.ids], dtype=float)
        self.rows.append((t, v))
        self.rows = [(u, x) for u, x in self.rows if u > t - self.window_s]
        if t % self.every_s or len(self.rows) * 2 < self.window_s // 300:
            return None
        m = np.vstack([x for _, x in self.rows])
        ok = np.isfinite(m).any(axis=0)
        r = np.full(m.shape[1], np.nan)
        r[ok] = np.nanmean(m[:, ok], axis=0)
        if not np.isfinite(r).any() or float(np.sqrt(np.nansum(r**2))) < self.alarm:
            return None
        hours = (np.array([u for u, _ in self.rows]) % DAY) // 3600
        order, sc = ranking(self.sens, r, hours, self.method)
        return order, [float(x) for x in sc]


def distances(g: nx.Graph, sources: list[str]) -> dict[str, dict[str, float]]:
    """Shortest-path pipe length (m) from each source node to every node."""
    return {s: nx.single_source_dijkstra_path_length(g, s, weight="weight") for s in sources}
