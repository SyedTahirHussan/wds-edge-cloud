"""Command-line entry point: ``flowmine <group> <command> [options]``."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from flowmine.config import load_config

Handler = Callable[[argparse.Namespace], int]
FROST_URL = "http://localhost:8080/FROST-Server/v1.1"


def _testbed_build(args: argparse.Namespace) -> int:
    from flowmine.testbed import build

    cfg = load_config(args.config)
    for seed in args.seeds or cfg.seeds:
        tb = build.ensure(cfg, seed, Path(args.data))
        print(
            f"seed {seed}: {tb.manifest['n_sensors']} sensors, {tb.manifest['n_faults']} faults, "
            f"{tb.manifest['n_events']} process events"
        )
    return 0


def _sta_wait(args: argparse.Namespace) -> int:
    from flowmine.sta.client import StaClient

    ok = StaClient(args.url).wait(args.timeout)
    print(f"{args.url}: {'up' if ok else 'not answering'}")
    return 0 if ok else 1


def _sta_provision(args: argparse.Namespace) -> int:
    from flowmine.sta.client import StaClient
    from flowmine.sta.provision import provision
    from flowmine.testbed import build

    cfg = load_config(args.config)
    tb = build.ensure(cfg, args.seed, Path(args.data))
    m = provision(StaClient(args.url), cfg, tb.sensors, tb.stations)
    print(f"{len(m.things)} things, {len(m.datastreams)} datastreams")
    return 0


def _exp_run(args: argparse.Namespace) -> int:
    from flowmine.experiments import run

    run.run_all(load_config(args.config), Path(args.out))
    return 0


def _exp_report(args: argparse.Namespace) -> int:
    from flowmine.experiments import report

    report.write(
        load_config(args.config), Path(args.results), None if args.no_readme else Path(args.readme)
    )
    print(f"wrote {Path(args.results) / 'summary.md'}")
    return 0


def _exp_footprint(args: argparse.Namespace) -> int:
    from flowmine.experiments import footprint

    df = footprint.measure(load_config(args.config), Path(args.out), args.seed)
    print(df.to_string(index=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="flowmine", description=__doc__)
    p.add_argument("-v", "--verbose", action="store_true")
    groups = p.add_subparsers(dest="group", required=True)

    tb = groups.add_parser("testbed", help="simulated ground truth").add_subparsers(
        dest="cmd", required=True
    )
    b = tb.add_parser("build", help="generate and cache the testbed for each seed")
    b.add_argument("--config", default="configs/default.yaml")
    b.add_argument("--data", default="data")
    b.add_argument("--seeds", type=int, nargs="*")
    b.set_defaults(func=_testbed_build)

    sta = groups.add_parser("sta", help="SensorThings server").add_subparsers(
        dest="cmd", required=True
    )
    w = sta.add_parser("wait", help="wait until the server answers")
    w.add_argument("--url", default=FROST_URL)
    w.add_argument("--timeout", type=float, default=180.0)
    w.set_defaults(func=_sta_wait)
    pv = sta.add_parser("provision", help="create all entities for a testbed seed")
    pv.add_argument("--url", default=FROST_URL)
    pv.add_argument("--config", default="configs/default.yaml")
    pv.add_argument("--data", default="data")
    pv.add_argument("--seed", type=int, default=1)
    pv.set_defaults(func=_sta_provision)

    ex = groups.add_parser("experiments", help="E1-E7").add_subparsers(dest="cmd", required=True)
    r = ex.add_parser("run", help="run all experiments over the configured seeds")
    r.add_argument("--config", default="configs/default.yaml")
    r.add_argument("--out", default="results")
    r.set_defaults(func=_exp_run)
    rp = ex.add_parser("report", help="write summary.md, figures and the README block")
    rp.add_argument("--config", default="configs/default.yaml")
    rp.add_argument("--results", default="results")
    rp.add_argument("--readme", default="README.md")
    rp.add_argument("--no-readme", action="store_true")
    rp.set_defaults(func=_exp_report)
    fp = ex.add_parser("footprint", help="edge parameters, sizes and per-sample timings")
    fp.add_argument("--config", default="configs/default.yaml")
    fp.add_argument("--out", default="results/edge_footprint.csv")
    fp.add_argument("--seed", type=int, default=1)
    fp.set_defaults(func=_exp_footprint)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    handler: Handler = args.func
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
