"""SensorThings <-> EPANET bridge: a hydraulic model driven by validated observations.

The model is stepped with the EPANET 2.2 toolkit bundled with WNTR
(``wntr.epanet.toolkit.ENepanet``), hydraulics and water quality together, one
reporting period at a time. Before each period the bridge sets the boundary
conditions from the latest observations:

* tank levels (``EN_TANKLEVEL``) from the level sensors;
* the status of every pump that has its own flow meter (open when the measured flow
  exceeds ``pump_on_l_s``); the model's controls on those pumps are removed, other
  controls are kept;
* one demand multiplier for all junctions from the water balance
  D = sum(source inflows) - sum(tank inflows), the tank inflows being taken from the
  tank inlet meters or, when those are unusable, from the level change times the
  tank area; m = D / sum(base demands).

Inputs that are missing or flagged are replaced by the edge reconstruction when the
observation carries one; otherwise the last good value is held and the step is
marked degraded. The element mapping comes from the Thing properties (no separate
mapping file); any EPANET .inp works, including models exported by Giswater.
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import wntr
from wntr.epanet.toolkit import ENepanet
from wntr.epanet.util import EN

from flowmine.config import Config
from flowmine.testbed import network, simulate

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class BridgeSensor:
    """What the bridge needs to know about one measured stream (from Thing.properties)."""

    sensor_id: str
    kind: str
    element: str
    element_type: str
    station: str
    tank_area_m2: float | None = None
    inflow_sign: float = 1.0
    period_s: int = 300


def sensors_from_things(things: list[dict[str, object]]) -> list[BridgeSensor]:
    """Read the element mapping from SensorThings Thing bodies."""
    out = []
    for t in things:
        props = t.get("properties") or {}
        assert isinstance(props, dict)
        for s in props.get("sensors", []):
            out.append(
                BridgeSensor(
                    s["sensor_id"],
                    s["kind"],
                    s["epanet_element"],
                    s["epanet_type"],
                    str(t["name"]),
                    props.get("tank_area_m2"),
                    float(props.get("inflow_sign") or 1.0),
                    int(s.get("period_s", 300)),
                )
            )
    return out


@dataclass
class StepInputs:
    """Boundary values for one period (physical units, already validated)."""

    levels: dict[str, float]  # tank id -> level (m)
    pump_flows: dict[str, float]  # pump id -> measured flow (L/s)
    demand_l_s: float | None = None  # total demand; None -> keep the previous value
    node_demands: dict[str, float] | None = None  # per-junction L/s (reference runs only)
    degraded: bool = False


@dataclass
class StepOutputs:
    t: int
    pressure: dict[str, float]
    flow: dict[str, float]
    chlorine: dict[str, float]
    degraded: bool


@dataclass
class Bridge:
    inp: str | None
    step_s: int
    duration_s: int
    measured_pumps: list[str]
    report_nodes: list[str]
    report_links: list[str]
    cfg: Config
    pump_on_l_s: float = 1.0
    level_tol_m: float = 0.2
    _tmp: tempfile.TemporaryDirectory[str] = field(init=False, repr=False)
    en: ENepanet = field(init=False, repr=False)
    base: dict[str, float] = field(init=False)
    t: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        wn = network.load_network(self.inp)
        network.convert_time_controls(wn)
        for name, ctl in list(wn.controls()):
            targets = {a.target()[0].name for a in ctl.actions()}
            if targets & set(self.measured_pumps):
                wn.remove_control(name)
        n_steps = self.duration_s // self.step_s + 1
        self.base = simulate.assign_demands(wn, np.ones((n_steps, len(wn.junction_name_list))))
        network.configure(wn, self.cfg, self.duration_s)
        for j in wn.junction_name_list:
            pat = f"m_{j}"
            if pat in wn.pattern_name_list:
                wn.get_pattern(pat).multipliers = [1.0]
        self._tmp = tempfile.TemporaryDirectory()
        path = Path(self._tmp.name) / "twin.inp"
        wntr.network.write_inpfile(wn, str(path), units="LPS")
        self.en = ENepanet()
        self.en.ENopen(str(path), str(path.with_suffix(".rpt")), str(path.with_suffix(".bin")))
        self.idx_node = {n: self.en.ENgetnodeindex(n) for n in wn.node_name_list}
        self.idx_link = {n: self.en.ENgetlinkindex(n) for n in wn.link_name_list}
        self.junctions = [j for j in wn.junction_name_list if self.base.get(j, 0.0) > 0]
        self.base_total_l_s = 1000.0 * sum(self.base[j] for j in self.junctions)
        self.multiplier = 1.0
        tank_links = {lk for t in wn.tank_name_list for lk in wn.get_links_for_node(t)}
        # Pipes next to a tank are closed temporarily by EPANET when the tank is full or
        # empty; carrying that state into a re-initialisation would close them for good.
        self.carry_links = [i for name, i in self.idx_link.items() if name not in tank_links]
        self.limits = {
            t: (
                self.en.ENgetnodevalue(self.idx_node[t], EN.MINLEVEL) + 1e-3,
                self.en.ENgetnodevalue(self.idx_node[t], EN.MAXLEVEL) - 1e-3,
            )
            for t in wn.tank_name_list
        }
        self.en.ENopenH()
        self.en.ENinitH(0)
        self.en.ENopenQ()
        self.en.ENinitQ(0)
        self.done = False
        self.offset = 0
        self.resyncs = 0

    def model_levels(self) -> dict[str, float]:
        """Current model tank levels (EN_PRESSURE at a tank is its level; EN_TANKLEVEL
        returns the initial level)."""
        return {t: self.en.ENgetnodevalue(self.idx_node[t], EN.PRESSURE) for t in self.limits}

    def _clip(self, tank: str, level: float) -> float:
        lo, hi = self.limits[tank]
        return min(max(level, lo), hi)

    def _resync(self, levels: dict[str, float]) -> None:
        """Re-initialise hydraulics at the current time with measured tank levels.

        Node chlorine and link statuses are carried over (except for pipes attached to a
        tank, whose temporary closures EPANET manages itself), so only the tank state changes.
        The EPANET clock restarts at zero (the toolkit refused to set the start time of
        day), so time-of-day controls on unmeasured links would shift; Net3 has none
        once the Lake pump is driven by its meter.
        """
        qual = {i: self.en.ENgetnodevalue(i, EN.QUALITY) for i in self.idx_node.values()}
        status = {i: self.en.ENgetlinkvalue(i, EN.STATUS) for i in self.carry_links}
        current = self.model_levels()
        self.en.ENcloseQ()
        self.en.ENcloseH()
        for tank in self.limits:
            lvl = levels.get(tank, current[tank])
            self.en.ENsetnodevalue(self.idx_node[tank], EN.TANKLEVEL, self._clip(tank, lvl))
        for i, q in qual.items():
            self.en.ENsetnodevalue(i, EN.INITQUAL, q)
        for i, st in status.items():
            self.en.ENsetlinkvalue(i, EN.INITSTATUS, st)
        self.en.ENsettimeparam(EN.DURATION, max(self.duration_s - self.t, self.step_s))
        self.en.ENopenH()
        self.en.ENinitH(0)
        self.en.ENopenQ()
        self.en.ENinitQ(0)
        self.offset = self.t
        self.resyncs += 1

    def _apply(self, inp: StepInputs) -> None:
        for pump, q in inp.pump_flows.items():
            self.en.ENsetlinkvalue(
                self.idx_link[pump], EN.STATUS, 1.0 if q > self.pump_on_l_s else 0.0
            )
        if inp.node_demands is not None:
            for j, q in inp.node_demands.items():
                if j in self.idx_node:
                    self.en.ENsetnodevalue(self.idx_node[j], EN.BASEDEMAND, float(q))
            return
        if inp.demand_l_s is not None and self.base_total_l_s > 0:
            self.multiplier = max(inp.demand_l_s, 0.0) / self.base_total_l_s
        for j in self.junctions:
            self.en.ENsetnodevalue(
                self.idx_node[j], EN.BASEDEMAND, 1000.0 * self.base[j] * self.multiplier
            )

    def step(self, inp: StepInputs) -> StepOutputs:
        """Advance one period with the given boundary values; returns values at its start.

        When a measured tank level differs from the model's by more than
        ``level_tol_m`` the model is re-synchronised first (see ``_resync``).
        """
        if self.t == 0 and inp.levels:
            self._resync(inp.levels)
        elif inp.levels:
            model = self.model_levels()
            if any(abs(model[k] - v) > self.level_tol_m for k, v in inp.levels.items()):
                self._resync(inp.levels)
        self._apply(inp)
        out: StepOutputs | None = None
        target = self.t + self.step_s
        while True:
            t = self.en.ENrunH() + self.offset
            self.en.ENrunQ()
            if out is None and t >= self.t:
                out = StepOutputs(
                    self.t,
                    {
                        n: self.en.ENgetnodevalue(self.idx_node[n], EN.PRESSURE)
                        for n in self.report_nodes
                    },
                    {
                        lk: self.en.ENgetlinkvalue(self.idx_link[lk], EN.FLOW)
                        for lk in self.report_links
                    },
                    {
                        n: self.en.ENgetnodevalue(self.idx_node[n], EN.QUALITY)
                        for n in self.report_nodes
                    },
                    inp.degraded,
                )
            dt = self.en.ENnextH()
            self.en.ENnextQ()
            if dt <= 0:
                self.done = True
                break
            if t + dt >= target:
                break
        self.t = target
        assert out is not None
        return out

    def close(self) -> None:
        self.en.ENcloseQ()
        self.en.ENcloseH()
        self.en.ENclose()
        self._tmp.cleanup()
