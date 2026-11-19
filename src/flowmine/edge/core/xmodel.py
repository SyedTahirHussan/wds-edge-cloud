"""Station-local cross-sensor model: each sensor predicted from the station's other sensors.

A pump station measures flow and discharge pressure, a tank station level and inlet
flow, a chlorine station pressure and chlorine. The cloud fits, on the training weeks,
a model that predicts one sensor y from the other sensor(s) o at the same time:

    features  f = [o, o - o_prev, o^2 for each input o] + [sin 2pi tau, cos 2pi tau,
                  sin 4pi tau, cos 4pi tau]            (tau = time of day in days)
    z = (f - mu) / sd
    ridge:  y_hat = ymu + ysd * (b + w . z)
    mlp:    y_hat = ymu + ysd * net(z)      (dense layers, ReLU hidden, linear output)

``o`` is the input's latest validated sample strictly before the target's time (so
the result does not depend on the order in which the station processes sensors that
report at the same instant) and ``o_prev`` the validated sample before it. The
residual e = y - y_hat in units of its training sd ``s`` is tested twice: against a
conformal threshold ``q`` from the calibration week (large errors), and by a
two-sided CUSUM with parameters ``cus = [k, h]`` (a persistent offset smaller than
``q``; station.py). This is the station's only test that can see a bias step: the
sensor's own forecaster re-levels after a step (pipeline.py), the other sensor does
not move.

A spec is a plain dict (it travels in the model package as JSON). MLP weights may be
int8 with one scale per tensor (``"q8": 1``): w = scale * int8.
"""

import math
from array import array

TWO_PI = 6.283185307179586
DAY = 86400


def features(vals, prevs, t):
    f = []
    for i in range(len(vals)):
        v = vals[i]
        f.append(v)
        f.append(v - prevs[i])
        f.append(v * v)
    a = TWO_PI * (t % DAY) / DAY
    f.append(math.sin(a))
    f.append(math.cos(a))
    f.append(math.sin(2.0 * a))
    f.append(math.cos(2.0 * a))
    return f


def load_layers(spec):
    """Layers as (n_in, n_out, act, weights, biases, scale); int8 weights stay int8."""
    out = []
    q8 = spec.get("q8", 0)
    for layer in spec["layers"]:
        n_in, n_out, act, w, b = layer[0], layer[1], layer[2], layer[3], layer[4]
        if q8:
            out.append((n_in, n_out, act, array("b", w), array("f", b), layer[5]))
        else:
            out.append((n_in, n_out, act, array("f", w), array("f", b), 1.0))
    return out


def forward(layers, x):
    for n_in, n_out, act, w, b, scale in layers:
        y = []
        for j in range(n_out):
            acc = 0.0
            base = j * n_in
            for i in range(n_in):
                acc += w[base + i] * x[i]
            acc = acc * scale + b[j]
            if act == 1 and acc < 0.0:
                acc = 0.0
            y.append(acc)
        x = y
    return x


class CrossModel:
    def __init__(self, spec):
        self.inputs = spec["inputs"]
        self.mu = array("f", spec["mu"])
        self.sd = array("f", spec["sd"])
        self.ymu = spec["ymu"]
        self.ysd = spec["ysd"]
        self.kind = spec["kind"]
        self.s = spec["s"]
        self.q = spec.get("q", 1.0e30)
        self.max_age = spec.get("max_age", 900)
        if self.kind == "ridge":
            self.w = array("f", spec["w"])
            self.b = spec["b"]
            self.layers = None
        else:
            self.w = None
            self.b = 0.0
            self.layers = load_layers(spec)

    def predict(self, vals, prevs, t):
        f = features(vals, prevs, t)
        z = []
        for i in range(len(f)):
            z.append((f[i] - self.mu[i]) / self.sd[i])
        if self.layers is None:
            acc = self.b
            for i in range(len(z)):
                acc += self.w[i] * z[i]
        else:
            acc = forward(self.layers, z)[0]
        return self.ymu + self.ysd * acc

    def residual(self, y, vals, prevs, t):
        """(y - y_hat) / s (residual in training sd units)."""
        return (y - self.predict(vals, prevs, t)) / self.s

    def score(self, y, vals, prevs, t):
        return abs(self.residual(y, vals, prevs, t))


class Recent:
    """The last few validated samples of one sensor, oldest first (fixed size)."""

    def __init__(self, size=4):
        self.size = size
        self.items = []

    def push(self, t, v):
        self.items.append((t, v))
        if len(self.items) > self.size:
            self.items.pop(0)

    def clear(self):
        self.items = []

    def at(self, t, max_age):
        """(value, previous value) of the latest sample before t, or None."""
        n = len(self.items)
        for i in range(n - 1, 0, -1):
            ti, vi = self.items[i]
            if ti < t:
                tp, vp = self.items[i - 1]
                if t - ti > max_age or ti - tp > max_age:
                    return None
                return vi, vp
        return None
