"""Score fusion for detection: a logistic model on edge and central evidence.

Features per sample: the edge rejected it (any test), the edge released it as a level
shift, the central spatial score |z| / threshold (capped at 10), and the product of
the shift flag with that score (a shift the other sensors do not confirm is likely a
fault). Fitted on week 7 with the labels; the decision threshold is the one that
maximises F1 on week 7. Nothing is tuned on the test week.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.linear_model import LogisticRegression


def features(reject: np.ndarray, shift: np.ndarray, zscore: np.ndarray) -> np.ndarray:
    z = np.clip(np.nan_to_num(zscore), 0.0, 10.0)
    return np.column_stack([reject, shift, z, shift * z]).astype(float)


@dataclass
class Fusion:
    model: LogisticRegression
    threshold: float

    @classmethod
    def fit(cls, x: np.ndarray, y: np.ndarray, seed: int) -> Fusion:
        m = LogisticRegression(C=1.0, class_weight="balanced", random_state=seed, max_iter=1000)
        m.fit(x, y)
        p = m.predict_proba(x)[:, 1]
        best_t, best_f = 0.5, -1.0
        for thr in np.linspace(0.05, 0.95, 19):
            pred = p >= thr
            tp = float((pred & y).sum())
            f1 = 2 * tp / max(float(pred.sum() + y.sum()), 1.0)
            if f1 > best_f:
                best_t, best_f = float(thr), f1
        return cls(m, best_t)

    def predict(self, x: np.ndarray) -> np.ndarray:
        return self.model.predict_proba(x)[:, 1] >= self.threshold
