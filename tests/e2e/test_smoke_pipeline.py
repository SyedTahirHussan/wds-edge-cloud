"""End to end on the smoke configuration: every experiment produces sane tables."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from flowmine.config import Config
from flowmine.experiments import report, run


def test_smoke_experiments_and_report(smoke_cfg: Config, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(Path(__file__).resolve().parents[2])
    run.run_all(smoke_cfg, tmp_path)
    for name in run.EXPERIMENTS:
        df = pd.read_csv(tmp_path / "raw" / f"{name}.csv")
        assert len(df) > 0 and (df.seed == 1).all()
    e1 = pd.read_csv(tmp_path / "raw" / "e1_detection.csv")
    assert set(e1.method) == {"rules", "edge_hw", "edge_mlp", "central", "hybrid"}
    rec = e1.set_index(["method", "fault_type"]).recall
    # The forecast residual catches spikes the rule tests miss, and the spatial model
    # catches the bias a single station cannot see.
    assert rec["edge_hw", "spike"] > rec["rules", "spike"]
    assert rec["central", "bias"] > rec["edge_hw", "bias"]
    e3 = pd.read_csv(tmp_path / "raw" / "e3_policies.csv").set_index("policy")
    assert (
        e3.loc["delta", "binary_bytes_per_station_day"]
        < e3.loc["validated", "binary_bytes_per_station_day"]
    )
    e6 = pd.read_csv(tmp_path / "raw" / "e6_twin.csv").set_index("input")
    assert e6.loc["clean", "rmse_monitored_m"] < e6.loc["raw", "rmse_monitored_m"]
    readme = tmp_path / "README.md"
    readme.write_text(f"intro\n{report.BEGIN}\nSTALE-BLOCK\n{report.END}\nend\n")
    report.write(smoke_cfg, tmp_path, readme)
    text = readme.read_text()
    assert "STALE-BLOCK" not in text and "E1 detection" in text and text.endswith("end\n")
    assert (tmp_path / "figures" / "e1_recall_by_fault.svg").exists()
