"""Cloud side of the v0.2 station: features match the station, calibration is sane."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from flowmine.config import Config
from flowmine.edge import replay, v2
from flowmine.edge.core import conformal
from flowmine.edge.core.xmodel import CrossModel, features
from flowmine.edge.models import PackagedModels
from flowmine.edge.node import model_package
from flowmine.testbed.build import Testbed


def test_numpy_features_match_the_station(smoke_tb: Testbed) -> None:
    wide = smoke_tb.wide("observed")
    per = {s.sensor_id: s.period_s for s in smoke_tb.sensors}
    st = next(s for s in smoke_tb.stations if s.station_class == "tank")
    target, inp = st.sensors[1], st.sensors[0]
    times, _, x = v2._input_rows(wide, target, [inp], per, *smoke_tb.periods["train"])
    for i in (5, 50, 500):
        t = int(times[i])
        last = (t - 1) // per[inp] * per[inp]
        v, p = float(wide.loc[last, inp]), float(wide.loc[last - per[inp], inp])
        np.testing.assert_allclose(x[i], features([v], [p], t), rtol=1e-6)


def test_xmodels_predict_the_same_in_numpy_and_pure_python(
    smoke_cfg: Config, smoke_tb: Testbed
) -> None:
    fits = v2.fit_xmodels(smoke_tb, smoke_cfg, epochs=3)
    assert {f.sensor_id for f in fits} == {
        s for st in smoke_tb.stations if len(st.sensors) > 1 for s in st.sensors
    }
    rng = np.random.default_rng(0)
    for f in fits:
        m = CrossModel(json.loads(json.dumps(f.spec)))
        vals = [float(rng.normal(f.spec["mu"][0], f.spec["sd"][0]))]
        z = (np.array(features(vals, vals, 3600)) - f.spec["mu"]) / f.spec["sd"]
        if f.chosen == "ridge":
            ref = f.spec["ymu"] + f.spec["ysd"] * float(z @ np.array(f.spec["w"]))
        else:
            from flowmine.central import nn

            assert f.net is not None
            ref = f.spec["ymu"] + f.spec["ysd"] * float(nn.forward(f.net, z[None, :])[0])
        assert abs(m.predict(vals, vals, 3600) - ref) < 1e-3 * max(1.0, abs(ref))
        assert f.spec["s"] > 0


def test_int8_layers_stay_close_to_float(smoke_cfg: Config, smoke_tb: Testbed) -> None:
    fits = [f for f in v2.fit_xmodels(smoke_tb, smoke_cfg, epochs=3) if f.net is not None]
    if not fits:
        return
    f = fits[0]
    q = {**f.spec, "layers": v2.quantise_layers(f.spec["layers"]), "q8": 1}
    a, b = CrossModel(f.spec), CrossModel(q)
    vals = [float(f.spec["mu"][0])]
    assert abs(a.predict(vals, vals, 0) - b.predict(vals, vals, 0)) < 0.05 * f.spec["ysd"]


def test_cusum_parameters_hit_the_clean_target() -> None:
    rng = np.random.default_rng(1)
    z = rng.normal(0, 1, 4000)
    z[3000:3400] += 1.0
    tr = pd.DataFrame(
        {
            "sensor_id": "A",
            "time_s": np.arange(4000) * 300,
            "z": z,
            "f": 0,
            "fault_type": np.where(
                (np.arange(4000) >= 3000) & (np.arange(4000) < 3400), "drift", ""
            ),
            "event_id": -1,
        }
    )
    params, choice = v2.cusum_params(tr, 0.002)
    k, h = params["A"]
    stat = v2.cusum_stat(z, k)
    assert (stat[:3000] > h).mean() <= 0.003
    assert (stat[3000:3400] > h).mean() > 0.2
    assert set(choice.k) == {0.25, 0.5, 0.75, 1.0}


def test_calibrated_package_roundtrips_and_station_runs(
    smoke_cfg: Config, smoke_tb: Testbed, smoke_bundles: dict
) -> None:
    fit = v2.calibrate(smoke_tb, smoke_bundles, smoke_cfg)
    b = fit.bundles
    assert all("conf_q" in m.meta and "cusum" in m.meta for m in b.values())
    multi = {s for st in smoke_tb.stations if len(st.sensors) > 1 for s in st.sensors}
    assert {sid for sid, m in b.items() if "xmodel" in m.meta} == multi
    sid = next(iter(multi))
    blob, _ = model_package(sid, 2, b[sid].hw_copy(), meta=b[sid].meta)
    pk = PackagedModels(blob)
    assert pk.meta["conf_q"] == b[sid].meta["conf_q"]
    assert pk.meta["xmodel"]["q"] < conformal.INF or pk.meta["xmodel"]["q"] == conformal.INF
    spec = smoke_tb.stations[0]
    node = replay.make_node(smoke_tb, spec, b, "validated")
    t0, t1 = smoke_tb.periods["test"]
    replay.run_station(node, smoke_tb, t0, t1)
    assert len(node.records) > 0
    # Without the v0.2 keys the station behaves exactly as v0.1.
    plain = replay.run_station(
        replay.make_node(smoke_tb, spec, v2.without(b, v2.COMPONENTS), "validated"),
        smoke_tb,
        t0,
        t1,
    )
    old = {k: m for k, m in smoke_bundles.items()}
    ref = replay.run_station(replay.make_node(smoke_tb, spec, old, "validated"), smoke_tb, t0, t1)
    assert plain.records == ref.records
