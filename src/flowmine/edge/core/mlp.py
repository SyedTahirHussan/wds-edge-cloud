"""Small neural forecaster: pure-Python forward pass over weights from a compact file.

Inputs for the next slot k+1 (all z-scored with the sensor's mean and std):
the last ``window`` values, the value one day earlier (slot k+1-s1), and sine/cosine
encodings of time of day and day of week. The network predicts the change from the
last value. Training happens in the cloud (flowmine.central.nn_train) and is exported
to the format below, which this module reads with ``struct`` only.

File format (little endian):
    header  '<4sBBHHHff'  magic b'FMW1', n_layers, window, s1, s2, period, mean, std
    layer   '<HHB'        n_in, n_out, activation (0 linear, 1 relu)
            n_out*n_in float32 weights (row-major, one row per output), n_out float32 biases
"""

import math
import struct
from array import array

MAGIC = b"FMW1"
HEADER = "<4sBBHHHff"
LAYER = "<HHB"
TWO_PI = 6.283185307179586


def parse(buf):
    """Decode a weight file into ``(meta, layers)``."""
    magic, n_layers, window, s1, s2, period, mean, std = struct.unpack_from(HEADER, buf, 0)
    if magic != MAGIC:
        raise ValueError("not a flowmine weight file")
    off = struct.calcsize(HEADER)
    layers = []
    for _ in range(n_layers):
        n_in, n_out, act = struct.unpack_from(LAYER, buf, off)
        off += struct.calcsize(LAYER)
        nw = n_in * n_out
        w = array("f", struct.unpack_from("<%df" % nw, buf, off))
        off += 4 * nw
        b = array("f", struct.unpack_from("<%df" % n_out, buf, off))
        off += 4 * n_out
        layers.append((n_in, n_out, act, w, b))
    meta = {"window": window, "s1": s1, "s2": s2, "period": period, "mean": mean, "std": std}
    return meta, layers


def forward(layers, x):
    """Dense layers with optional ReLU; ``x`` is a list of floats."""
    for n_in, n_out, act, w, b in layers:
        y = []
        for j in range(n_out):
            acc = b[j]
            base = j * n_in
            for i in range(n_in):
                acc += w[base + i] * x[i]
            if act == 1 and acc < 0.0:
                acc = 0.0
            y.append(acc)
        x = y
    return x


def n_params(layers):
    n = 0
    for n_in, n_out, _act, _w, _b in layers:
        n += n_in * n_out + n_out
    return n


def time_features(slot, s1, s2):
    a = TWO_PI * (slot % s1) / s1
    b = TWO_PI * (slot % s2) / s2
    return [math.sin(a), math.cos(a), math.sin(b), math.cos(b)]


class MLPForecaster:
    kind = "mlp"

    def __init__(self, buf, t0=0):
        meta, layers = parse(buf)
        self.layers = layers
        self.window = meta["window"]
        self.s1 = meta["s1"]
        self.s2 = meta["s2"]
        self.period = meta["period"]
        self.mean = meta["mean"]
        self.std = meta["std"]
        self.t0 = t0
        self.hist = array("f", [0.0] * self.s1)
        self.k = None

    def slot(self, t):
        return (t - self.t0) // self.period

    def _features(self, k, recent):
        """Features to predict slot k+1; ``recent`` overrides history for future slots."""
        x = []
        for i in range(self.window):
            x.append(self._z(k - i, recent))
        x.append(self._z(k + 1 - self.s1, recent))
        return x + time_features(k + 1, self.s1, self.s2)

    def _z(self, slot, recent):
        if recent is not None and slot > self.k:
            return recent[slot - self.k - 1]
        return self.hist[slot % self.s1]

    def _next_z(self, k, recent):
        return self._z(k, recent) + forward(self.layers, self._features(k, recent))[0]

    def update(self, x, t):
        """Append ``x`` (or, when None, the model's own forecast) to the input history."""
        k = self.slot(t)
        if self.k is None:
            self.k = k - 1
        if k <= self.k:
            return
        while self.k < k:
            nxt = self.k + 1
            if nxt == k and x is not None:
                z = (x - self.mean) / self.std
            else:
                z = self._next_z(self.k, None)
            self.hist[nxt % self.s1] = z
            self.k = nxt

    def predict(self, h):
        recent = []
        k = self.k
        for _ in range(h):
            recent.append(self._next_z(k, recent))
            k += 1
        return recent[-1] * self.std + self.mean

    def forecast_at(self, t):
        h = self.slot(t) - self.k
        if h < 1:
            h = 1
        return self.predict(h)

    def relevel(self, x):
        """After an accepted level shift, the lags restart from the new value."""
        z = (x - self.mean) / self.std
        for i in range(self.window):
            self.hist[(self.k - i) % self.s1] = z

    def state(self):
        return {"kind": self.kind, "t0": self.t0, "k": self.k, "hist": list(self.hist)}

    def load(self, s):
        self.t0 = s["t0"]
        self.k = s["k"]
        self.hist = array("f", s["hist"])
