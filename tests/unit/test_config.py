from pathlib import Path

import pytest

from flowmine.config import load_config


def test_default_and_smoke_load_with_inheritance() -> None:
    d = load_config("configs/default.yaml")
    s = load_config("configs/smoke.yaml")
    assert d.seeds == (1, 2, 3, 4, 5)
    assert s.seeds == (1,)
    assert s.network.train_days == 10 and s.total_days == 14
    assert s.sensors["pressure"].epsilon == d.sensors["pressure"].epsilon
    assert s.layout.n_pressure == 6
    assert d.digest() != s.digest()


def test_unknown_key_is_rejected(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("extends: %s\nnetwork: {train_dayz: 3}\n" % Path("configs/default.yaml").resolve())
    with pytest.raises(ValueError, match="train_dayz"):
        load_config(p)
