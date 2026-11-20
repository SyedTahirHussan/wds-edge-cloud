"""Heap use and per-sample time of one station, runnable under MicroPython and CPython.

``probe(path)`` reads ``station.json`` (pipelines with their Holt-Winters state and
parameters, optional tank and cross-sensor models) and ``samples.json`` (a list of
[sensor, t, value]) from ``path``, builds the station and feeds it the samples.

Under MicroPython it reports, in bytes of GC heap: ``built`` (live right after the
station is built), ``resident`` (live after it has processed all samples, after a
collection) and ``peak`` (resident plus the most bytes allocated while one sample is
processed, with a collection before each sample). MicroPython allocates every float
result on the heap, so the second part is mostly short-lived garbage that the
collector can reclaim; it is an upper bound on the transient need, not live data.
Under CPython the three are reported as -1. The time is the mean per sample. A
laptop's MicroPython Unix port is not an ESP32: the heap numbers depend on the port's
object layout, which is the same code base as on the ESP32 port, but the timings do
not carry over.
"""

import gc
import json
import time

from .holtwinters import HoltWinters
from .pipeline import SensorPipeline
from .station import Station


def _now_us():
    if hasattr(time, "ticks_us"):
        return time.ticks_us()
    return int(time.perf_counter() * 1000000)


def _alloc():
    if hasattr(gc, "mem_alloc"):
        return gc.mem_alloc()
    return -1


def build(spec):
    pipes = {}
    xm = {}
    for key in spec["pipelines"]:
        p = spec["pipelines"][key]
        st = p["state"]
        hw = HoltWinters(st["period"], st["s1"], st["s2"], 0.0, 0.0, 0.0, 0.0)
        hw.load(st)
        pipes[int(key)] = SensorPipeline(p["meta"], hw)
        if "xmodel" in p["meta"]:
            xm[int(key)] = p["meta"]["xmodel"]
    return Station(spec.get("number", 0), pipes, spec.get("tank"), xmodels=xm or None)


def probe(path):
    with open(path + "/station.json") as f:
        spec = json.load(f)
    with open(path + "/samples.json") as f:
        samples = json.load(f)
    n = len(samples)
    # Memory pass: collect before every sample; the growth during one sample is
    # the transient part of the peak.
    gc.collect()
    base = _alloc()
    st = build(spec)
    gc.collect()
    built = _alloc()
    peak = 0
    for s in samples:
        gc.collect()
        a = _alloc()
        st.process(s[0], s[1], s[2])
        b = _alloc()
        if b - a > peak:
            peak = b - a
    gc.collect()
    resident = _alloc()
    st = None
    # Timing pass on a fresh station, without forced collections.
    st = build(spec)
    gc.collect()
    t0 = _now_us()
    for s in samples:
        st.process(s[0], s[1], s[2])
    elapsed = _now_us() - t0
    if base < 0:
        return {"built": -1, "resident": -1, "peak": -1, "us_per_sample": elapsed / n}
    return {
        "built": built - base,
        "resident": resident - base,
        "peak": resident - base + peak,
        "us_per_sample": elapsed / n,
    }


def main(path):
    r = probe(path)
    print("built", r["built"])
    print("resident", r["resident"])
    print("peak", r["peak"])
    print("us_per_sample", r["us_per_sample"])
