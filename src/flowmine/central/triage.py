"""Event triage: sensor fault or process event?

For every window of ``window`` grid steps the triage looks at two kinds of evidence:

* temporal: which sensors the edge marked as anomalous (residual rejection or level
  shift) in the window;
* spatial: which sensors the central models find inconsistent with the others
  (|z| above threshold).

A sensor that is anomalous in time *and* inconsistent with the rest, while at most
``max_faulty`` sensors are inconsistent, is a sensor fault: the cloud replaces its
values with the spatial estimate. When at least ``min_event`` sensors are temporally
anomalous in the same window and most of them stay spatially consistent, the change
is hydraulically coherent: a process event is raised and nothing is reconstructed.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class TriageResult:
    fault: np.ndarray  # (n, m) bool, treat as sensor fault (reconstruct)
    event: np.ndarray  # (n,) bool, process event raised in this window
    event_sensors: np.ndarray  # (n, m) bool, sensors taking part in the event


def triage(
    temporal: np.ndarray, spatial: np.ndarray, window: int, min_event: int = 2, max_faulty: int = 2
) -> TriageResult:
    n, m = temporal.shape
    fault = np.zeros((n, m), dtype=bool)
    event = np.zeros(n, dtype=bool)
    part = np.zeros((n, m), dtype=bool)
    for w0 in range(0, n, window):
        sl = slice(w0, min(n, w0 + window))
        t_any = temporal[sl].any(axis=0)
        s_any = spatial[sl].any(axis=0)
        n_incons = int(s_any.sum())
        coherent = t_any & ~s_any
        if int(t_any.sum()) >= min_event and coherent.sum() >= max(1, int(t_any.sum()) // 2):
            event[sl] = True
            part[sl] = t_any[None, :]
        if 0 < n_incons <= max_faulty:
            cand = s_any & (t_any | ~event[sl].any())
            fault[sl] = spatial[sl] & cand[None, :]
    return TriageResult(fault, event, part)
