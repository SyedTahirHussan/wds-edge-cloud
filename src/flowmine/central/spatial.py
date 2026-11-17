"""Spatial consistency models over the hydraulic sensors (pressure, flow, level).

(a) PCA on standardised training data, keeping the components that explain
``pca_variance`` of the variance. A sample vector x is projected on the model plane
using only its usable entries: t = (P_o' P_o)^-1 P_o' x_o, x_hat = P t, and the
squared prediction error SPE = |x_o - x_hat_o|^2 is the alarm statistic. Entries the
edge flagged count as missing, i.e. they get zero weight as inputs, and x_hat gives
their reconstruction.

(b) Each sensor regressed on all others at lags 0..L (ridge). Inputs flagged by the
edge are first replaced by their PCA reconstruction. The standardised residual
z_i = (x_i - x_hat_i) / sd_i is the per-sensor alarm statistic and x_hat_i the
reconstruction of a rejected sensor.

Both are trained on weeks 1-6; thresholds are set on clean samples of week 7.
This follows the spatial-model stage of the validation/reconstruction methodology
of Quevedo et al. (2010) and Cugueró-Escofet et al. (2016), re-implemented here with
generic linear models instead of their network-specific relations.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class SpatialResult:
    estimate: np.ndarray  # (n, m) physical units, ridge reconstruction
    z: np.ndarray  # (n, m) standardised ridge residual
    spe: np.ndarray  # (n,)
    imputed: np.ndarray  # (n, m) physical units, PCA reconstruction


@dataclass
class SpatialModel:
    streams: list[str]
    lags: int
    alpha: float
    variance: float
    mean: np.ndarray = field(init=False)
    std: np.ndarray = field(init=False)
    loadings: np.ndarray = field(init=False)
    coefs: list[np.ndarray] = field(init=False)
    resid_sd: np.ndarray = field(init=False)
    z_thr: np.ndarray = field(init=False)
    spe_thr: float = field(init=False, default=np.inf)

    def _std(self, x: np.ndarray) -> np.ndarray:
        return (x - self.mean) / self.std

    def _features(self, zfull: np.ndarray, i: int) -> np.ndarray:
        others = np.delete(zfull, i, axis=1)
        cols = [others]
        for lag in range(1, self.lags + 1):
            sh = np.vstack([np.repeat(others[:1], lag, axis=0), others[:-lag]])
            cols.append(sh)
        cols.append(np.ones((len(zfull), 1)))
        return np.hstack(cols)

    def fit(self, train: np.ndarray) -> SpatialModel:
        rows = np.isfinite(train).all(axis=1)
        x = train[rows]
        self.mean = x.mean(axis=0)
        self.std = x.std(axis=0)
        self.std[self.std == 0] = 1.0
        z = self._std(x)
        _, s, vt = np.linalg.svd(z, full_matrices=False)
        frac = np.cumsum(s**2) / np.sum(s**2)
        k = int(np.searchsorted(frac, self.variance) + 1)
        self.loadings = vt[: min(k, z.shape[1] - 1)].T
        self.coefs, sds = [], []
        for i in range(z.shape[1]):
            f = self._features(z, i)
            a = f.T @ f + self.alpha * np.eye(f.shape[1])
            c = np.linalg.solve(a, f.T @ z[:, i])
            self.coefs.append(c)
            sds.append(float(np.std(z[:, i] - f @ c)) or 1e-6)
        self.resid_sd = np.array(sds)
        self.z_thr = np.full(len(self.streams), np.inf)
        return self

    @property
    def n_components(self) -> int:
        return int(self.loadings.shape[1])

    def pca_impute(self, z: np.ndarray, usable: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Project each row on the PCA plane using only usable entries."""
        p = self.loadings
        zhat = np.zeros_like(z)
        spe = np.full(len(z), np.nan)
        keys = np.packbits(usable, axis=1)
        _, inverse = np.unique(keys, axis=0, return_inverse=True)
        for g in np.unique(inverse):
            rows = np.where(inverse == g)[0]
            obs = usable[rows[0]]
            if obs.sum() < p.shape[1]:
                continue
            po = p[obs]
            t = np.linalg.solve(po.T @ po + 1e-6 * np.eye(p.shape[1]), po.T @ z[rows][:, obs].T).T
            zh = t @ p.T
            zhat[rows] = zh
            spe[rows] = np.sum((z[rows][:, obs] - zh[:, obs]) ** 2, axis=1)
        return zhat, spe

    def apply(self, values: np.ndarray, suspect: np.ndarray) -> SpatialResult:
        """Estimates and residuals; ``suspect`` entries are not used as inputs."""
        z = self._std(values)
        usable = ~suspect & np.isfinite(z)
        zfill = np.where(usable, z, 0.0)
        zhat, spe = self.pca_impute(zfill, usable)
        zin = np.where(usable, zfill, zhat)
        est = np.empty_like(z)
        res = np.full_like(z, np.nan)
        for i in range(z.shape[1]):
            pred = self._features(zin, i) @ self.coefs[i]
            est[:, i] = pred
            res[:, i] = (z[:, i] - pred) / self.resid_sd[i]
        return SpatialResult(est * self.std + self.mean, res, spe, zhat * self.std + self.mean)

    def calibrate(
        self, values: np.ndarray, suspect: np.ndarray, clean: np.ndarray, q: float
    ) -> None:
        """Thresholds = quantile ``q`` of |z| (per sensor) and SPE on clean samples."""
        r = self.apply(values, suspect)
        for i in range(len(self.streams)):
            zi = np.abs(r.z[clean[:, i], i])
            zi = zi[np.isfinite(zi)]
            self.z_thr[i] = float(np.quantile(zi, q)) if len(zi) else np.inf
        rows = clean.all(axis=1) & np.isfinite(r.spe)
        self.spe_thr = float(np.quantile(r.spe[rows], q)) if rows.any() else np.inf

    def flags(self, r: SpatialResult) -> np.ndarray:
        return np.abs(np.nan_to_num(r.z)) > self.z_thr
