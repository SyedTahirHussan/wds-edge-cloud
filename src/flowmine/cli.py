"""Command-line entry point: ``flowmine <group> <command> [options]``."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from flowmine.config import load_config

Handler = Callable[[argparse.Namespace], int]


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
