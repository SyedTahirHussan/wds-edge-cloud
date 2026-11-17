"""Command-line entry point: ``flowmine <group> <command> [options]``."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable, Sequence
from datetime import datetime
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


def _start(cfg_path: str) -> datetime:
    return datetime.fromisoformat(load_config(cfg_path).network.start)


def _edge_run(args: argparse.Namespace) -> int:
    from flowmine.edge import service

    return service.run(
        load_config(args.config),
        args.seed,
        args.station,
        args.gateway,
        Path(args.registry),
        args.t0,
        args.t1,
        args.speed,
        args.policy,
        Path(args.store),
    )


def _gateway(args: argparse.Namespace) -> int:
    import uvicorn

    from flowmine.hybrid.registry import ModelRegistry
    from flowmine.sta.client import StaClient
    from flowmine.sta.gateway_app import create_app
    from flowmine.sta.provision import load_map
    from flowmine.testbed import build

    cfg = load_config(args.config)
    tb = build.ensure(cfg, args.seed, Path(args.data))
    client = StaClient(args.url)
    app = create_app(
        client,
        load_map(client),
        tb.stations,
        tb.sensors,
        _start(args.config),
        ModelRegistry(Path(args.registry)) if args.registry else None,
        cfg.edge.summary_s,
    )
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


def _central_run(args: argparse.Namespace) -> int:
    from flowmine.central import service
    from flowmine.sta.client import StaClient

    n = service.run(
        StaClient(args.url), load_config(args.config), args.seed, _start(args.config), args.interval
    )
    print(f"central analytics posted {n} events")
    return 0


def _twin_run(args: argparse.Namespace) -> int:
    from urllib.parse import urlsplit

    from flowmine.sta.client import StaClient
    from flowmine.twin import service

    cfg = load_config(args.config)
    client = StaClient(args.url)
    if args.mode == "replay":
        n = service.replay(client, cfg, _start(args.config), args.t0, args.t1)
    else:
        host = urlsplit(args.url).hostname or "localhost"
        n = service.live(
            client, cfg, _start(args.config), host, args.mqtt_port, args.t0, args.t1 - args.t0 + 600
        )
    print(f"twin posted {n} observations")
    return 0


def _dashboard(args: argparse.Namespace) -> int:
    import uvicorn

    from flowmine.dashboard.app import create_app
    from flowmine.sta.client import StaClient

    app = create_app(StaClient(args.url), load_config(args.config).network.inp)
    print(f"dashboard on http://127.0.0.1:{args.port}/")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


def _demo(args: argparse.Namespace) -> int:
    from flowmine import demo

    return demo.run(
        load_config(args.config),
        args.config,
        args.url,
        args.seed,
        args.hours,
        args.speed,
        args.gateway_port,
        args.dashboard_port,
        args.keep,
        Path(args.data),
    )


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

    def common(q: argparse.ArgumentParser, url: bool = True) -> None:
        q.add_argument("--config", default="configs/default.yaml")
        q.add_argument("--seed", type=int, default=1)
        q.add_argument("--data", default="data")
        if url:
            q.add_argument("--url", default=FROST_URL)

    ed = groups.add_parser("edge", help="edge station").add_subparsers(dest="cmd", required=True)
    er = ed.add_parser("run", help="run one station process (demo)")
    common(er, url=False)
    er.add_argument("--station", required=True)
    er.add_argument("--gateway", required=True)
    er.add_argument("--registry", required=True)
    er.add_argument("--t0", type=int, required=True)
    er.add_argument("--t1", type=int, required=True)
    er.add_argument("--speed", type=float, default=600.0)
    er.add_argument("--policy", default="validated")
    er.add_argument("--store", default="data/stations")
    er.set_defaults(func=_edge_run)

    gw = groups.add_parser("gateway", help="frame decoder and SensorThings forwarder")
    common(gw)
    gw.add_argument("--port", type=int, default=8090)
    gw.add_argument("--registry", default="")
    gw.set_defaults(func=_gateway)

    ce = groups.add_parser("central", help="central analytics").add_subparsers(
        dest="cmd", required=True
    )
    cr = ce.add_parser("run", help="analytics loop on live SensorThings data")
    common(cr)
    cr.add_argument("--interval", type=float, default=5.0)
    cr.set_defaults(func=_central_run)

    tw = groups.add_parser("twin", help="SensorThings-EPANET bridge").add_subparsers(
        dest="cmd", required=True
    )
    tr = tw.add_parser("run", help="run the bridge (replay stored data or live via MQTT)")
    common(tr)
    tr.add_argument("--mode", choices=["replay", "live"], default="replay")
    tr.add_argument("--t0", type=int, required=True)
    tr.add_argument("--t1", type=int, required=True)
    tr.add_argument("--mqtt-port", type=int, default=1883)
    tr.set_defaults(func=_twin_run)

    db = groups.add_parser("dashboard", help="local live web view")
    common(db)
    db.add_argument("--port", type=int, default=8050)
    db.set_defaults(func=_dashboard)

    dm = groups.add_parser("demo", help="full system against FROST-Server")
    common(dm)
    dm.add_argument("--hours", type=float, default=48.0)
    dm.add_argument("--speed", type=float, default=1200.0)
    dm.add_argument("--gateway-port", type=int, default=8090)
    dm.add_argument("--dashboard-port", type=int, default=8050)
    dm.add_argument("--keep", action="store_true", help="keep the dashboard running afterwards")
    dm.set_defaults(func=_demo)
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
