"""Edge-core tests written for both CPython (via pytest) and the MicroPython Unix port.

Only plain asserts and the standard library subset MicroPython ships are used, so
``micropython tests/micropython/run.py`` executes exactly these functions.
"""

import math
import random
import struct

from flowmine.edge.core import checks, codec, comm, conformal, quality
from flowmine.edge.core.crosscheck import TankBalance
from flowmine.edge.core.cusum import Cusum
from flowmine.edge.core.dualpred import DeltaReceiver, DeltaSender
from flowmine.edge.core.holtwinters import HoltWinters
from flowmine.edge.core.mlp import MLPForecaster, forward, n_params, parse
from flowmine.edge.core.pipeline import SensorPipeline
from flowmine.edge.core.residual import ResidualTest
from flowmine.edge.core.ringbuf import RingBuffer
from flowmine.edge.core.snaive import SeasonalNaive
from flowmine.edge.core.station import Station
from flowmine.edge.core.xmodel import CrossModel, Recent, features

P = 300
S1 = 12
S2 = 24
NTRAIN = 60


def meta(**kw):
    m = {
        "period": P,
        "t0": 0,
        "lo": 0.0,
        "hi": 100.0,
        "max_rate": 0.01,
        "flat_window": 4,
        "flat_tol": 0.01,
        "rest_band": 0.0,
        "k": 4.0,
        "lam": 0.05,
        "init_scale": 0.1,
        "min_scale": 0.02,
        "shift_after": 4,
        "max_recon": 50,
    }
    for key in kw:
        m[key] = kw[key]
    return m


def seasonal(k):
    return 50.0 + 5.0 * math.sin(6.283185307179586 * (k % S1) / S1) + (k % S2 >= S1) * 1.0


def trained_hw():
    hw = HoltWinters(P, S1, S2, 0.1, 0.1, 0.1, 0.5)
    for k in range(S2 * NTRAIN):
        hw.update(seasonal(k), k * P)
    return hw


def mlp_bytes(n_in, hidden, window, s1, s2, mean, std, seed=1):
    rnd = random.Random(seed) if hasattr(random, "Random") else None
    rand = rnd.random if rnd is not None else random.random
    buf = bytearray(struct.pack("<4sBBHHHff", b"FMW1", 2, window, s1, s2, P, mean, std))
    for a, b, act in ((n_in, hidden, 1), (hidden, 1, 0)):
        buf += struct.pack("<HHB", a, b, act)
        buf += struct.pack("<%df" % (a * b), *[(rand() - 0.5) * 0.2 for _ in range(a * b)])
        buf += struct.pack("<%df" % b, *[(rand() - 0.5) * 0.1 for _ in range(b)])
    return bytes(buf)


def test_quality_code_roundtrip():
    c = quality.make(quality.MISSING | quality.RECONSTRUCTED, 261)
    assert quality.flags(c) == quality.MISSING | quality.RECONSTRUCTED
    assert quality.model_version(c) == 261 % 256
    assert quality.names(c) == ["MISSING", "RECONSTRUCTED"]
    assert quality.is_suspect(c)
    assert not quality.is_suspect(quality.make(quality.RECONSTRUCTED, 3))


def test_ringbuffer_order():
    rb = RingBuffer(3)
    for x in (1.0, 2.0, 3.0, 4.0):
        rb.push(x)
    assert rb.values() == [2.0, 3.0, 4.0]
    assert rb.get(0) == 4.0 and rb.full()


def test_grid_missing_duplicate_out_of_order():
    g = comm.TimeGrid(P, 0)
    assert g.check(0) == (comm.OK, 0)
    assert g.check(P) == (comm.OK, 0)
    assert g.check(P) == (comm.DUPLICATE, 0)
    assert g.check(4 * P + 10) == (comm.OK, 2)
    assert g.check(2 * P) == (comm.OUT_OF_ORDER, 0)
    assert (g.missing, g.duplicates, g.out_of_order) == (2, 1, 1)


def test_range_and_rate():
    assert checks.in_range(5.0, 0.0, 10.0) and not checks.in_range(-0.1, 0.0, 10.0)
    assert checks.rate_ok(1.0, None, 0, 0.1)
    assert checks.rate_ok(1.0, 0.0, 300, 0.01)
    assert not checks.rate_ok(4.0, 0.0, 300, 0.01)


def test_flatline_uses_resolution_and_rest_band():
    f = checks.Flatline(4, 0.01)
    flags = [f.push(x) for x in (1.0, 1.005, 1.0, 1.005, 1.0)]
    assert flags == [False, False, False, True, True]
    g = checks.Flatline(4, 0.01)
    assert [g.push(x) for x in (1.0, 1.02, 1.0, 1.02)] == [False] * 4
    h = checks.Flatline(3, 0.01, rest_band=0.5)
    assert [h.push(0.0) for _ in range(5)] == [False] * 5


def test_residual_threshold_adapts_and_resists_outliers():
    r = ResidualTest(4.0, 0.1, 1.0, 0.01)
    for _ in range(200):
        r.learn(0.1)
    assert abs(r.scale - 0.1) < 1e-3
    thr = r.threshold()
    assert r.test(0.9 * thr) and not r.test(1.1 * thr)
    r.learn(1000.0)
    assert r.scale < 0.1 * (1 + 0.1 * 3.0) + 1e-9


def test_tank_balance():
    tb = TankBalance(100.0, 0.02)
    assert tb.level(0, 5.0)
    tb.flow(0, 10.0)
    tb.flow(450, 10.0)
    assert tb.level(900, 5.0 + 10.0 * 0.001 * 900 / 100.0)
    tb.flow(900, 0.0)
    assert not tb.level(1800, 5.2)


def test_snaive_predicts_last_season():
    m = SeasonalNaive(P, S2)
    for k in range(S2 * 2):
        m.update(seasonal(k), k * P)
    k = S2 * 2 - 1
    assert abs(m.predict(1) - seasonal(k + 1)) < 1e-4
    m.update(None, (k + 1) * P)
    assert abs(m.predict(1) - seasonal(k + 2)) < 1e-4


def test_holtwinters_learns_double_seasonal_pattern():
    hw = trained_hw()
    k = S2 * NTRAIN - 1
    for h in (1, 5, S1):
        assert abs(hw.predict(h) - seasonal(k + h)) < 0.3


def test_holtwinters_missing_does_not_change_state():
    hw = trained_hw()
    lv = hw.level
    d = list(hw.d)
    hw.update(None, hw.k * P + P)
    assert hw.level == lv and list(hw.d) == d
    hw.update(seasonal(hw.k + 3), (hw.k + 3) * P)
    assert hw.k == S2 * NTRAIN + 3


def test_holtwinters_state_roundtrip():
    hw = trained_hw()
    other = HoltWinters(P, S1, S2, 0.0, 0.0, 0.0, 0.0)
    other.load(hw.state())
    assert abs(other.predict(3) - hw.predict(3)) < 1e-9


def test_mlp_parse_and_forward_shapes():
    buf = mlp_bytes(17, 8, 12, S1 * 2, S2 * 2, 50.0, 5.0)
    meta_, layers = parse(buf)
    assert meta_["window"] == 12 and len(layers) == 2
    assert n_params(layers) == 17 * 8 + 8 + 8 + 1
    y = forward(layers, [0.0] * 17)
    assert len(y) == 1


def test_mlp_forward_matches_manual():
    buf = bytearray(struct.pack("<4sBBHHHff", b"FMW1", 1, 1, 4, 8, P, 0.0, 1.0))
    buf += struct.pack("<HHB", 2, 1, 1) + struct.pack("<2f", 2.0, -1.0) + struct.pack("<f", 0.5)
    _, layers = parse(bytes(buf))
    assert forward(layers, [1.0, 1.0]) == [1.5]
    assert forward(layers, [0.0, 3.0]) == [0.0]


def test_mlp_recursive_matches_stepwise():
    buf = mlp_bytes(17, 8, 12, S1 * 2, S2 * 2, 50.0, 5.0)
    m = MLPForecaster(buf)
    for k in range(60):
        m.update(seasonal(k), k * P)
    p3 = m.predict(3)
    m.update(None, 60 * P)
    m.update(None, 61 * P)
    assert abs(m.predict(1) - p3) < 1e-4


def test_codec_single_reading_is_12_bytes_and_roundtrips():
    scales = {3: 0.01}
    fr = codec.encode(7, 2, 1000, [(3, 1300, 45.678, 16)], scales)
    assert len(fr) == 12
    st, mv, recs = codec.decode(fr, scales)
    assert st == 7 and mv == 2
    sid, t, v, q = recs[0]
    assert sid == 3 and t == 1300 and q == 16 and abs(v - 45.68) < 1e-9


def test_codec_summary_and_saturation():
    scales = {1: 0.1, 2: 0.01}
    recs = [(1, 0, 1.0, 0.5, 1.5, 0, "s"), (2, 60, 999.0, 0)]
    fr = codec.encode(1, 0, 0, recs, scales)
    assert len(fr) == 6 + 10 + 6
    _, _, out = codec.decode(fr, scales)
    assert out[0][6] == "s" and abs(out[0][4] - 1.5) < 1e-9
    assert abs(out[1][2] - 327.67) < 1e-9


def test_codec_batch_respects_payload_limit():
    scales = {0: 0.01}
    recs = [(0, i * P, 40.0 + i * 0.01, 0) for i in range(40)]
    frames = codec.batch(1, 0, recs, scales, 51)
    assert all(len(f) <= 51 for f in frames)
    got = []
    for f in frames:
        got.extend(codec.decode(f, scales)[2])
    assert len(got) == 40 and got[-1][1] == 39 * P
    big = codec.batch(1, 0, recs, scales, 222)
    assert len(big) == 2 and len(big[0]) == 6 + 32 * 6


def test_dual_prediction_error_bound_randomised():
    rnd = random.Random(3) if hasattr(random, "Random") else random
    for trial in range(5):
        eps = 0.05 + 0.1 * trial
        tx = DeltaSender(trained_hw(), eps, 3600, 0.01)
        rx = DeltaReceiver(trained_hw())
        k0 = S2 * NTRAIN
        for i in range(150):
            k = k0 + i
            v = seasonal(k) + (rnd.random() - 0.5) * 0.6 + (2.0 if i > 80 else 0.0)
            sent, held = tx.offer(k * P, v)
            est = rx.step(k * P, held if sent else None)
            assert abs(est - held) < 1e-9
            assert abs(est - v) <= eps + 0.005 + 1e-9


def test_pipeline_flags_spike_and_reconstructs():
    p = SensorPipeline(meta(shift_after=3), trained_hw(), 5)
    k0 = S2 * NTRAIN
    out = []
    for i in range(10):
        k = k0 + i
        x = seasonal(k) + (8.0 if i == 5 else 0.0)
        out.extend(p.process(k * P, x))
    spike = out[5]
    assert spike[3] & quality.MODEL_RESIDUAL and spike[3] & quality.RECONSTRUCTED
    assert spike[1] == seasonal(k0 + 5) + 8.0
    assert abs(spike[2] - seasonal(k0 + 5)) < 0.3
    assert quality.model_version(spike[3]) == 5
    assert all(quality.flags(r[3]) == 0 for i, r in enumerate(out) if i != 5)


def test_pipeline_gap_reconstruction_and_out_of_range():
    p = SensorPipeline(meta(), trained_hw())
    k0 = S2 * NTRAIN
    out = p.process(k0 * P, seasonal(k0))
    out += p.process((k0 + 4) * P, seasonal(k0 + 4))
    assert [quality.flags(r[3]) for r in out[1:4]] == [quality.MISSING | quality.RECONSTRUCTED] * 3
    assert all(
        r[1] is None and abs(r[2] - seasonal(k0 + 1 + i)) < 0.3 for i, r in enumerate(out[1:4])
    )
    bad = p.process((k0 + 5) * P, 150.0)[0]
    assert bad[3] & quality.OUT_OF_RANGE and bad[3] & quality.RECONSTRUCTED
    assert p.process((k0 + 5) * P, 1.0) == [] and p.dropped == 1


def test_pipeline_snaps_jittered_timestamps_to_the_slot():
    hw = trained_hw()
    p = SensorPipeline(meta(), hw)
    k0 = S2 * NTRAIN
    out = p.process(k0 * P - 3, seasonal(k0))
    out += p.process((k0 + 1) * P + 2, seasonal(k0 + 1))
    assert [r[0] for r in out] == [k0 * P, (k0 + 1) * P]
    assert hw.k == k0 + 1


def test_pipeline_level_shift_is_accepted_after_k_rejections():
    p = SensorPipeline(meta(max_rate=1.0, shift_after=3), trained_hw())
    k0 = S2 * NTRAIN
    recs = []
    released_at = []
    for i in range(10):
        k = k0 + i
        out = p.process(k * P, seasonal(k) - (3.0 if i >= 3 else 0.0))
        released_at.append(len(out))
        recs.extend(out)
    assert released_at[3:6] == [0, 0, 3]
    flags = [quality.flags(r[3]) for r in recs]
    assert flags[3:6] == [quality.SHIFT] * 3
    assert all(abs(r[2] - r[1]) < 1e-12 for r in recs[3:6])
    assert all(f == 0 for f in flags[6:]) and len(recs) == 10
    assert abs(recs[9][2] - (seasonal(k0 + 9) - 3.0)) < 1e-9


def test_pipeline_transient_is_released_as_reconstructed():
    p = SensorPipeline(meta(shift_after=3, max_rate=1.0), trained_hw())
    k0 = S2 * NTRAIN
    recs = []
    for i in range(6):
        k = k0 + i
        recs.extend(p.process(k * P, seasonal(k) + (5.0 if i in (2, 3) else 0.0)))
    flags = [quality.flags(r[3]) for r in recs]
    assert flags[2:4] == [quality.MODEL_RESIDUAL | quality.RECONSTRUCTED] * 2
    assert abs(recs[3][2] - seasonal(k0 + 3)) < 0.3 and len(recs) == 6
    p.process((k0 + 6) * P, seasonal(k0 + 6) + 5.0)
    held = p.flush()
    assert len(held) == 1 and held[0][3] & quality.RECONSTRUCTED


def test_pipeline_flatline_on_stuck_sensor():
    p = SensorPipeline(meta(shift_after=100), trained_hw())
    k0 = S2 * NTRAIN
    recs = []
    for i in range(8):
        x = seasonal(k0) if i >= 2 else seasonal(k0 + i)
        recs.extend(p.process((k0 + i) * P, x))
    recs.extend(p.flush())
    flags = [quality.flags(r[3]) for r in sorted(recs)]
    assert len(flags) == 8 and flags[0] == 0
    assert flags[5] & quality.FLATLINE and flags[7] & quality.FLATLINE


def test_station_crosscheck_flags_level_and_flow():
    lv = SensorPipeline(meta(period=900, k=50.0, flat_window=100, max_rate=1.0), None)
    fl = SensorPipeline(meta(k=50.0, flat_window=100, max_rate=10.0), None)
    st = Station(1, {0: lv, 1: fl}, {"level": 0, "flow": 1, "area_m2": 100.0, "tol_m": 0.02})
    out = st.process(0, 0, 5.0)
    out += st.process(1, 0, 0.0)
    for k in range(1, 4):
        out += st.process(1, k * P, 0.0)
    out += st.process(0, 900, 5.3)
    lvl = [r for r in out if r[0] == 0]
    assert lvl[1][4] & quality.CROSSCHECK
    flows = [r for r in out if r[0] == 1]
    assert len(flows) == 4 and flows[0][4] == 0
    assert all(r[4] & quality.CROSSCHECK for r in flows[1:])
    assert st.flush() == []
    out = st.process(1, 1200, 2.0)
    assert out == [] and len(st.flush()) == 1


# ---- v0.2 additions -------------------------------------------------------------


def test_conformal_rank_and_quantile():
    assert conformal.rank(99, 0.1) == 90
    assert conformal.rank(9, 0.1) == 9
    scores = [float(i) for i in range(1, 100)]
    assert conformal.split_quantile(scores, 0.1) == 90.0
    # Too few samples for the level: the threshold is infinite.
    assert conformal.split_quantile([1.0, 2.0], 0.1) == conformal.INF
    assert conformal.split_quantile([], 0.1) == conformal.INF


def _gauss(rand):
    return sum(rand() for _ in range(12)) - 6.0


def _rand(seed):
    if hasattr(random, "Random"):
        return random.Random(seed).random
    random.seed(seed)
    return random.random


def test_conformal_coverage_on_exchangeable_scores():
    rand = _rand(3)
    cal = [abs(_gauss(rand)) for _ in range(199)]
    q = conformal.split_quantile(cal, 0.1)
    miss = 0
    for _ in range(2000):
        if abs(_gauss(rand)) > q:
            miss += 1
    # The realised miss rate of one calibration set is random (Beta); bounds are loose.
    assert 0.05 < miss / 2000 < 0.16


def test_conformal_buckets_and_halfwidth():
    table = [1.0, 2.0, 3.0, 4.0]
    assert conformal.bucket(0, 4) == 0 and conformal.bucket(86399, 4) == 3
    assert conformal.threshold(table, 86400 + 7 * 3600) == 2.0
    assert conformal.threshold([5.0], 12345) == 5.0
    iv = [(900, 1.5), (3600, 3.0)]
    assert conformal.halfwidth(iv, 300, 2.0) == 3.0
    assert conformal.halfwidth(iv, 3600, 2.0) == 6.0
    assert conformal.halfwidth(iv, 7200, 2.0) == conformal.INF


def test_cusum_detects_small_mean_shift_and_resets():
    c = Cusum(0.5, 5.0)
    assert not any(c.update(z) for z in [0.3, -0.4, 0.2, -0.1] * 20)
    c.reset()
    hits = [c.update(1.0) for _ in range(20)]
    assert not hits[0] and hits[-1] and hits.index(True) == 10
    c.reset()
    assert c.stat() == 0.0 and not c.alarm()
    # A single clipped outlier cannot raise the alarm.
    assert not Cusum(0.5, 5.0).update(1000.0)
    c2 = Cusum(0.5, 5.0)
    assert any(c2.update(-1.0) for _ in range(12))


def test_pipeline_cusum_flags_slow_drift():
    m = meta(max_rate=1.0, shift_after=50, cusum=[0.5, 4.0])
    p = SensorPipeline(m, trained_hw())
    k0 = S2 * NTRAIN
    rand = _rand(5)
    recs = []
    for i in range(400):
        k = k0 + i
        drift = 0.004 * (i - 100) if i > 100 else 0.0
        recs.extend(p.process(k * P, seasonal(k) + drift + 0.02 * _gauss(rand)))
    flagged = [i for i, r in enumerate(recs) if r[3] & quality.MODEL_RESIDUAL]
    assert flagged and min(flagged) > 100
    assert all(not recs[i][3] & quality.RECONSTRUCTED for i in flagged)
    clean = SensorPipeline(meta(max_rate=1.0, shift_after=50), trained_hw())
    for i in range(50):
        clean.process((k0 + i) * P, seasonal(k0 + i))
    assert clean.cusum is None


def test_pipeline_conformal_table_replaces_k():
    p = SensorPipeline(meta(conf_q=[1.0e30], max_rate=1.0), trained_hw())
    k0 = S2 * NTRAIN
    out = []
    for i in range(6):
        k = k0 + i
        out.extend(p.process(k * P, seasonal(k) + (8.0 if i == 3 else 0.0)))
    assert all(quality.flags(r[3]) == 0 for r in out)
    trace = []
    p2 = SensorPipeline(meta(conf_q=[2.0, 2.0]), trained_hw())
    p2.trace = trace
    p2.process(k0 * P, seasonal(k0) + 8.0)
    assert len(trace) == 1 and trace[0][1] > 2.0 and trace[0][2] & quality.MODEL_RESIDUAL


def _ridge_spec(inputs, w, s, q):
    return {
        "inputs": inputs,
        "mu": [0.0] * 7,
        "sd": [1.0] * 7,
        "ymu": 0.0,
        "ysd": 1.0,
        "kind": "ridge",
        "w": w,
        "b": 0.0,
        "s": s,
        "q": q,
        "max_age": 900,
    }


def test_xmodel_features_ridge_and_int8_mlp():
    f = features([2.0], [1.5], 21600)
    assert len(f) == 7 and f[0] == 2.0 and f[1] == 0.5 and f[2] == 4.0
    assert abs(f[3] - 1.0) < 1e-9 and abs(f[4]) < 1e-9
    m = CrossModel(_ridge_spec([1], [2.0, 0, 0, 0, 0, 0, 0], 0.1, 3.0))
    assert abs(m.predict([3.0], [3.0], 0) - 6.0) < 1e-6
    assert abs(m.score(6.5, [3.0], [3.0], 0) - 5.0) < 1e-5
    layers = [
        [7, 2, 1, [1, 0, 0, 0, 0, 0, 0, -1, 0, 0, 0, 0, 0, 0], [0.0, 0.0], 0.5],
        [2, 1, 0, [127, 127], [0.25], 0.01],
    ]
    spec = _ridge_spec([1], [0.0] * 7, 1.0, 3.0)
    spec["kind"] = "mlp"
    spec["layers"] = layers
    spec["q8"] = 1
    mm = CrossModel(spec)
    # hidden = relu(0.5 * [x, -x]); out = 0.01 * 127 * (h0 + h1) + 0.25
    assert abs(mm.predict([2.0], [2.0], 0) - (1.27 * 1.0 + 0.25)) < 1e-5
    assert abs(mm.predict([-2.0], [-2.0], 0) - (1.27 * 1.0 + 0.25)) < 1e-5


def test_recent_latest_sample_and_staleness():
    r = Recent(3)
    assert r.at(100, 900) is None
    r.push(0, 1.0)
    r.push(300, 2.0)
    r.push(600, 3.0)
    r.push(900, 4.0)
    assert len(r.items) == 3 and r.at(700, 900) == (3.0, 2.0)
    assert r.at(900, 900) == (3.0, 2.0) and r.at(901, 900) == (4.0, 3.0)
    assert r.at(3000, 900) is None


def _flat_hw(level):
    hw = HoltWinters(P, S1, S2, 0.1, 0.1, 0.1, 0.5)
    for k in range(S2 * 40):
        hw.update(level + (0.01 if k % 2 else -0.01), k * P)
    return hw


def _pair_station():
    # Sensor 0 reads 50, sensor 1 reads 25; each is predicted from the other (0 = 2 * 1).
    m = meta(max_rate=10.0, shift_after=2, min_scale=0.05, flat_window=200)
    st = Station(
        2,
        {0: SensorPipeline(m, _flat_hw(50.0)), 1: SensorPipeline(m, _flat_hw(25.0))},
        None,
        xmodels={
            0: _ridge_spec([1], [2.0, 0, 0, 0, 0, 0, 0], 0.05, 4.0),
            1: _ridge_spec([0], [0.5, 0, 0, 0, 0, 0, 0], 0.05, 4.0),
        },
    )
    st.tie_s = 1800
    return st


def _run_pair(st, a_step, b_step, n=40, onset=10):
    k0 = S2 * 40
    out = []
    for i in range(n):
        k = k0 + i
        on = i >= onset
        w = 0.01 if k % 2 else -0.01
        out += st.process(0, k * P, 50.0 + w + (a_step if on else 0.0))
        out += st.process(1, k * P, 25.0 + w / 2 + (b_step if on else 0.0))
    out += st.flush()
    a = sorted(r for r in out if r[0] == 0)
    b = sorted(r for r in out if r[0] == 1)
    return a, b


def test_station_cross_model_blames_the_sensor_that_shifted():
    a, b = _run_pair(_pair_station(), 3.0, 0.0)
    flagged = [i for i, r in enumerate(a) if r[4] & quality.CROSSCHECK]
    # Evidence (the level shift) at sample 10; flags start tie_s (6 samples) later.
    assert flagged == list(range(16, 40))
    assert not any(r[4] & quality.CROSSCHECK for r in b)


def test_station_cross_model_tie_flags_nothing():
    # Both sensors move together (a real change): no cross-model flag.
    a, b = _run_pair(_pair_station(), 3.0, 1.5)
    assert not any(r[4] & quality.CROSSCHECK for r in a + b)
