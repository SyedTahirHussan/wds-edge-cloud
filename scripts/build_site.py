"""Write site/data.json for the project page from the network and the committed results.

Every number on the page comes from this file, and this file comes from
``results/raw/*.csv`` (written by ``make reproduce``) and the EPANET network in
``configs/default.yaml``. Run with ``make site``.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from flowmine.config import load_config
from flowmine.testbed import network
from flowmine.testbed.sensors import layout

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "results" / "raw"
OUT = ROOT / "site" / "data.json"


def r(x: float, n: int = 4) -> float | None:
    return None if x is None or not np.isfinite(x) else round(float(x), n)


def seed_stats(df: pd.DataFrame, keys: list[str], col: str) -> list[dict]:
    """Mean and sample sd over seeds of the per-seed mean, as in results/summary.md."""
    per_seed = df.groupby(["seed", *keys])[col].mean()
    g = per_seed.groupby(keys)
    out = []
    for k, s in g:
        k = k if isinstance(k, tuple) else (k,)
        out.append({**dict(zip(keys, k, strict=True)), "mean": r(s.mean()), "sd": r(s.std(ddof=1))})
    return out


def net_geometry(cfg) -> dict:
    wn = network.load_network(cfg.network.inp)
    xy = network.coordinates(wn)
    g = network.graph(wn)
    sensors, stations = layout(wn, g, cfg)
    links = []
    for name in wn.link_name_list:
        lk = wn.get_link(name)
        pts = [xy[lk.start_node_name], *getattr(lk, "vertices", []), xy[lk.end_node_name]]
        links.append(
            {"id": name, "type": lk.link_type.lower(), "pts": [[r(x, 1), r(y, 1)] for x, y in pts]}
        )
    nodes = [
        {
            "id": n,
            "type": wn.get_node(n).node_type.lower(),
            "x": r(xy[n][0], 1),
            "y": r(xy[n][1], 1),
        }
        for n in wn.node_name_list
        if wn.get_node(n).node_type != "Junction"
    ]
    by_id = {s.sensor_id: s for s in sensors}
    st = [
        {
            "id": s.station_id,
            "class": s.station_class,
            "node": s.node,
            "x": r(xy[s.node][0], 1),
            "y": r(xy[s.node][1], 1),
            "sensors": [f"{by_id[i].kind} ({i})" for i in s.sensors],
        }
        for s in stations
    ]
    return {"links": links, "nodes": nodes, "stations": st, "n_sensors": len(sensors)}


def main() -> None:
    cfg = load_config(ROOT / "configs" / "default.yaml")
    d = {f.stem: pd.read_csv(f) for f in sorted(RAW.glob("*.csv"))}

    e1 = d["e1_detection"]
    faults = ["spike", "stuck", "noise_burst", "bias", "drift"]
    e1_recall = seed_stats(e1[e1.fault_type.isin(faults)], ["method", "fault_type"], "recall")
    ov = e1[e1.fault_type == "overall"]
    e1_overall = {
        m: {
            "f1": next(x for x in seed_stats(ov, ["method"], "f1") if x["method"] == m),
            "false_alarm_rate": next(
                x for x in seed_stats(ov, ["method"], "false_alarm_rate") if x["method"] == m
            ),
        }
        for m in sorted(ov.method.unique())
    }

    e2 = d["e2_reconstruction"]
    e2_mae = seed_stats(e2[e2.kind == "pressure"], ["method", "gap_min"], "mae")

    e3 = d["e3_policies"]
    e3_bytes = seed_stats(e3, ["policy"], "binary_bytes_per_station_day")
    e3_json = seed_stats(e3, ["policy"], "json_bytes_per_station_day")

    e5 = d["e5_training"]
    e5_cold = seed_stats(e5[e5.cold_start], ["paradigm"], "one_step_rmse_m")
    e5_day = seed_stats(e5[e5.cold_start], ["paradigm"], "day_ahead_rmse_m")

    e6 = d["e6_twin"]
    e6_unmon = seed_stats(e6, ["input"], "rmse_unmonitored_m")

    e4 = d["e4_outage"]
    e4_rows = (
        e4.groupby(["outage_h", "architecture"])[
            ["events_flagged_during_outage", "fault_events_in_window", "backfill_h"]
        ]
        .mean()
        .reset_index()
    )

    e8a = d["e8a_conformal"]
    e8a_rows = seed_stats(e8a[e8a.period == "test"], ["buckets", "alpha"], "empirical_rate")
    e8b = d["e8b_intervals"]
    e8b_cov = seed_stats(e8b, ["method", "gap_min"], "coverage")
    ov2 = ov[ov.method.isin(["edge_hw", "edge_v2"])]
    phase1 = {
        "conformal": e8a_rows,
        "coverage": e8b_cov,
        "nominal_coverage": 0.9,
        "v1_v2": {
            m: {
                k: next(x for x in seed_stats(ov2, ["method"], k) if x["method"] == m)
                for k in ("f1", "false_alarm_rate")
            }
            for m in ("edge_hw", "edge_v2")
        },
    }
    fps = ROOT / "results" / "edge_footprint_stations.csv"
    if fps.exists():
        f = pd.read_csv(fps)
        phase1["footprint"] = [
            {
                "station": str(r["station"]),
                "cls": str(r["station_class"]),
                "version": str(r["version"]),
                "heap": int(r["heap_resident_bytes"]),
                "package": int(r["package_bytes"]),
                "us": round(float(r["us_per_sample_micropython"]), 1),
            }
            for r in f.to_dict("records")
        ]

    data = {
        "seeds": cfg.seeds,
        "weeks": (cfg.network.train_days + cfg.network.calib_days + cfg.network.test_days) // 7,
        "network": net_geometry(cfg),
        "e1": {"recall": e1_recall, "overall": e1_overall, "faults": faults},
        "e2": {"pressure_mae": e2_mae},
        "e3": {"binary": e3_bytes, "json": e3_json},
        "e4": [
            {k: r(v, 3) if isinstance(v, float) else v for k, v in row.items()}
            for row in e4_rows.to_dict("records")
        ],
        "e5": {"cold_one_step": e5_cold, "cold_day_ahead": e5_day},
        "e6": {"unmonitored": e6_unmon},
        "phase1": phase1,
    }
    OUT.write_text(json.dumps(data, separators=(",", ":")) + "\n")
    print(f"wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
