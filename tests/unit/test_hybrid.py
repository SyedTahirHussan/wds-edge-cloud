from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from flowmine.central import nn
from flowmine.config import Config
from flowmine.edge import replay
from flowmine.edge.models import PackagedModels
from flowmine.edge.node import model_package
from flowmine.hybrid import federated
from flowmine.hybrid.fusion import Fusion, features
from flowmine.hybrid.registry import ModelRegistry
from flowmine.testbed.build import Testbed


def _logger(tb: Testbed):
    return next(s for s in tb.stations if s.station_class == "logger")


def test_registry_versions_promote_rollback_and_deploy(
    tmp_path: Path, smoke_tb: Testbed, smoke_bundles
) -> None:
    reg = ModelRegistry(tmp_path)
    spec = _logger(smoke_tb)
    sid = spec.sensors[0]
    b = smoke_bundles[sid]
    e1 = reg.register(sid, "hw", model_package(sid, 1, b.hw_copy(), meta=b.meta)[0], (0, 10), {})
    e2 = reg.register(
        sid, "hw", model_package(sid, 2, b.hw_copy(), meta=b.meta)[0], (0, 20), {"mae": 0.1}
    )
    assert (e1.version, e2.version) == (1, 2) and reg.active(sid) is None
    reg.promote(sid, 1)
    reg.promote(sid, 2)
    assert reg.active(sid).version == 2 and reg.versions(sid)[0].status == "retired"
    assert ModelRegistry(tmp_path).active(sid).version == 2
    t0 = smoke_tb.periods["test"][0]
    node = replay.run_station(
        replay.make_node(smoke_tb, spec, smoke_bundles, "validated"), smoke_tb, t0, t0 + 3600
    )
    assert reg.deploy(node, sid) and node.model_version(sid) == 2
    assert reg.deployed(list(spec.sensors)) == 2
    assert reg.rollback(sid).version == 1 and reg.active(sid).version == 1
    (tmp_path / sid / "v1.json").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="corrupted"):
        reg.blob(reg.active(sid))


def test_packaged_models_drive_a_node(smoke_tb: Testbed, smoke_bundles) -> None:
    spec = _logger(smoke_tb)
    pk = {
        s: PackagedModels(
            model_package(s, 3, smoke_bundles[s].hw_copy(), meta=smoke_bundles[s].meta)[0]
        )
        for s in spec.sensors
    }
    t0 = smoke_tb.periods["test"][0]
    a = replay.run_station(
        replay.make_node(smoke_tb, spec, pk, "validated"), smoke_tb, t0, t0 + 6 * 3600
    )
    b = replay.run_station(
        replay.make_node(smoke_tb, spec, smoke_bundles, "validated"), smoke_tb, t0, t0 + 6 * 3600
    )
    assert [r[3] for r in a.records] == [r[3] for r in b.records]
    with pytest.raises(ValueError):
        PackagedModels(model_package(spec.sensors[0], 1, smoke_bundles[spec.sensors[0]].mlp())[0])


def _toy_series(n_st: int, days: int) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(0)
    k = np.arange(days * 288)
    return {
        f"S{i}": 40 + 3 * np.sin(2 * np.pi * k / 288 + i) + rng.normal(0, 0.1, len(k))
        for i in range(n_st)
    }


def test_training_paradigms_account_bytes(smoke_cfg: Config) -> None:
    series = _toy_series(3, 9)
    loc = federated.local(series, smoke_cfg, 300, 1)
    cen = federated.centralised(series, smoke_cfg, 300, 1)
    fed = federated.federated(series, smoke_cfg, 300, 1, rounds=2, local_epochs=1, fine_tune=False)
    ft = federated.federated(series, smoke_cfg, 300, 1, rounds=2, local_epochs=1, fine_tune=True)
    wb = federated.weight_bytes(next(iter(fed.nets.values())))
    assert loc.bytes_up == loc.bytes_down == 0
    assert cen.bytes_up == sum(federated.raw_upload_bytes(len(y)) for y in series.values())
    assert cen.bytes_down == 3 * wb
    assert fed.bytes_up == fed.bytes_down == 2 * 3 * wb
    w = [m.w1 for m in fed.nets.values()]
    assert np.allclose(w[0], w[1]) and not np.allclose(ft.nets["S0"].w1, ft.nets["S1"].w1)
    assert not np.allclose(loc.nets["S0"].w1, loc.nets["S1"].w1)
    m = cen.nets["S0"]
    y = series["S0"]
    err = federated.day_ahead_rmse(m, y, y, [288 * 7])
    assert np.isfinite(err) and err < 3.0
    assert nn.one_step_mse(m, (y - m.mean) / m.std) < 1.0


def test_fusion_learns_a_separating_rule() -> None:
    rng = np.random.default_rng(0)
    n = 4000
    y = rng.random(n) < 0.1
    reject = (y & (rng.random(n) < 0.5)) | (rng.random(n) < 0.01)
    shift = rng.random(n) < 0.05
    z = np.where(y, rng.uniform(1.5, 4, n), rng.uniform(0, 1.1, n))
    f = Fusion.fit(features(reject, shift, z), y, 0)
    pred = f.predict(features(reject, shift, z))
    assert (pred & y).sum() / y.sum() > 0.8 and (pred & ~y).sum() / (~y).sum() < 0.05
