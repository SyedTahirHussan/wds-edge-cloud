"""Shared fixtures: a small testbed generated once per test session."""

from __future__ import annotations

from pathlib import Path

import pytest

from flowmine.config import Config, load_config
from flowmine.testbed import build


@pytest.fixture(scope="session")
def smoke_cfg() -> Config:
    return load_config("configs/smoke.yaml")


@pytest.fixture(scope="session")
def smoke_tb(smoke_cfg: Config, tmp_path_factory: pytest.TempPathFactory) -> build.Testbed:
    root: Path = tmp_path_factory.mktemp("data")
    return build.ensure(smoke_cfg, 1, root)


@pytest.fixture(scope="session")
def smoke_bundles(smoke_cfg: Config, smoke_tb: build.Testbed) -> dict:
    from flowmine.edge.models import fit_sensor

    return {s.sensor_id: fit_sensor(smoke_tb, s.sensor_id, smoke_cfg) for s in smoke_tb.sensors}
