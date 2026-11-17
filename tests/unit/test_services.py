"""Gateway app, dashboard and central analytics against the fake SensorThings server."""

from __future__ import annotations

from fastapi.testclient import TestClient

from flowmine.central import homogenise
from flowmine.central import service as central
from flowmine.config import Config
from flowmine.dashboard.app import create_app as dashboard_app
from flowmine.dashboard.app import state_of
from flowmine.edge import replay
from flowmine.edge.core import quality
from flowmine.sta.client import StaClient
from flowmine.sta.fake import create_app as fake_app
from flowmine.sta.gateway_app import create_app as gateway_app
from flowmine.sta.provision import provision
from flowmine.testbed.build import Testbed


def test_gateway_dashboard_roundtrip(smoke_cfg: Config, smoke_tb: Testbed, smoke_bundles) -> None:
    sta = StaClient("http://testserver/v1.1", TestClient(fake_app(base_url="http://testserver")))
    m = provision(sta, smoke_cfg, smoke_tb.sensors, smoke_tb.stations, ["123"])
    start = replay.start_time(smoke_cfg)
    gw = TestClient(gateway_app(sta, m, smoke_tb.stations, smoke_tb.sensors, start))
    spec = next(s for s in smoke_tb.stations if s.station_class == "logger")
    t0 = smoke_tb.periods["test"][0]
    node = replay.run_station(
        replay.make_node(smoke_tb, spec, smoke_bundles, "validated"), smoke_tb, t0, t0 + 3 * 3600
    )
    for _, frame in node.link.frames:
        assert gw.post(f"/uplink/{spec.station_id}", content=frame).status_code == 200
    assert gw.post("/uplink/NOPE", content=b"x").status_code == 404
    assert gw.post(f"/uplink/{spec.station_id}", content=b"\x00\x01").status_code == 400
    stats = {"validated": {"binary_bytes": 100, "json_bytes": 900, "frames": 3}}
    assert (
        gw.post(f"/stats/{spec.station_id}", json={"t": t0, "policies": stats}).status_code == 200
    )
    assert gw.get("/health").json()["frames"] == len(node.link.frames)

    db = TestClient(dashboard_app(sta, None))
    assert "<svg" in db.get("/").text
    net = db.get("/api/network").json()
    st = next(s for s in net["stations"] if s["id"] == spec.station_id)
    rank = ["none", "ok", "shift", "reconstructed", "missing", "flagged"]
    last = {}
    for r in node.records:
        if r[1] >= last.get(r[0], (-1, 0))[0]:
            last[r[0]] = (r[1], r[4])
    expected = max((state_of(q) for _, q in last.values()), key=rank.index)
    assert st["state"] == expected
    assert st["latest"] is not None and len(net["links"]) > 100
    sid = spec.sensors[0]
    series = db.get(f"/api/series/{sid}").json()
    assert len(series["measured"]["t"]) == 36 and series["simulated"]["t"] == []
    assert db.get("/api/counters").json()["policies"]["validated"]["frames"] == 3
    assert db.get("/api/events").json() == []


def test_central_analysis_reports_a_biased_sensor(smoke_cfg: Config, smoke_tb: Testbed) -> None:
    streams = [s.sensor_id for s in smoke_tb.sensors if s.kind != "chlorine"]
    model = central.trained_model(smoke_cfg, smoke_tb, streams)
    t0 = smoke_tb.periods["test"][0]
    frame = homogenise.raw_frame(smoke_tb.truth, t0, t0 + 6 * 3600)
    p = next(s for s in streams if s.startswith("P-") and s != "P-10")
    sel = (frame.stream == p) & (frame.time_s >= t0 + 3600)
    frame.loc[sel, "value"] += 8.0
    frame.loc[sel, "quality"] = quality.make(quality.SHIFT, 1)
    per = {s.sensor_id: s.period_s for s in smoke_tb.sensors}
    g = homogenise.align(frame, streams, t0, t0 + 6 * 3600, 300, per)
    events = central.analyse(model, g, smoke_cfg.central.triage_window)
    faults = [e for e in events if e["type"] == "sensor_fault"]
    assert faults and all(p in e["sensors"] for e in faults[:3])


def test_twin_replay_and_live_step_post_simulated_streams(
    smoke_cfg: Config, smoke_tb: Testbed, monkeypatch
) -> None:
    from flowmine.sta import mapping
    from flowmine.twin import service as twin

    sta = StaClient("http://testserver/v1.1", TestClient(fake_app(base_url="http://testserver")))
    m = provision(sta, smoke_cfg, smoke_tb.sensors, smoke_tb.stations, ["123"])
    start = replay.start_time(smoke_cfg)
    t0 = smoke_tb.periods["test"][0]
    t1 = t0 + 3 * 3600
    tr = smoke_tb.truth[(smoke_tb.truth.time_s >= t0) & (smoke_tb.truth.time_s < t1)]
    bodies = [
        mapping.observation_body(start, int(t), float(v), 0, datastream_id=m.datastreams[s])
        for s, t, v in zip(tr.sensor_id, tr.time_s, tr.value, strict=True)
    ]
    sta.post_observations(bodies)
    n = twin.replay(sta, smoke_cfg, start, t0, t1)
    assert n > 0
    sim = sta.load_frame(
        {"SIM-P-15": m.datastreams["SIM-P-15"], "VIRT-123": m.datastreams["VIRT-123"]}, start
    )
    assert len(sim[sim.stream == "SIM-P-15"]) == 36 and len(sim[sim.stream == "VIRT-123"]) == 36

    sensors = twin.sensors_from_things(twin.station_things(sta))
    rows = sta.load_frame(m.measured([s.sensor_id for s in sensors]), start, t0, t0 + 1800)

    live = twin.LiveTwin(smoke_cfg, sensors, ["123"], t0, 3 * 3600 + 600)
    for r in rows.to_dict("records"):
        live.add(r)
    steps = []
    while live.ready(30.0):
        steps.append(live.step())
    step_s = smoke_cfg.network.hydraulic_step_s
    # Every period with a complete set of streams is stepped; nothing is held after.
    assert [s[0] for s in steps] == list(range(t0, t0 + 1800, step_s))
    assert not live.pending()
    # Fed with the truth, the simulated pressure follows the measured one.
    assert all(abs(s[1]["SIM-P-15"] - s[2]["P-15"]) < 2.0 for s in steps)
    live.bridge.close()

    # A stream that has gone silent holds the step until the wall-clock fallback runs
    # out (one that already reported a later time would not: its sample is missing).
    clock = [0.0]
    monkeypatch.setattr(twin.time, "monotonic", lambda: clock[0])
    live = twin.LiveTwin(smoke_cfg, sensors, ["123"], t0, 3 * 3600 + 600)
    for r in rows.to_dict("records"):
        if r["stream"] != "P-15":
            live.add(r)
    assert not live.ready(30.0) and live.pending()
    clock[0] = 29.0
    assert not live.ready(30.0)
    clock[0] = 31.0
    assert live.ready(30.0)
    t, _sim, measured = live.step()
    assert t == t0 and "P-15" not in measured
    live.bridge.close()


def test_edge_station_process_posts_through_gateway(
    smoke_cfg: Config, smoke_tb: Testbed, smoke_bundles, tmp_path, monkeypatch
) -> None:
    from flowmine.edge import service as edge_service
    from flowmine.edge.node import model_package
    from flowmine.hybrid.registry import ModelRegistry

    sta = StaClient("http://testserver/v1.1", TestClient(fake_app(base_url="http://testserver")))
    m = provision(sta, smoke_cfg, smoke_tb.sensors, smoke_tb.stations)
    start = replay.start_time(smoke_cfg)
    reg = ModelRegistry(tmp_path / "reg")
    spec = next(s for s in smoke_tb.stations if s.station_class == "tank")
    for sid in spec.sensors:
        b = smoke_bundles[sid]
        e = reg.register(sid, "hw", model_package(sid, 1, b.hw_copy(), meta=b.meta)[0], (0, 1), {})
        reg.promote(sid, e.version)
    gw = TestClient(gateway_app(sta, m, smoke_tb.stations, smoke_tb.sensors, start, reg))
    monkeypatch.setattr(edge_service.httpx, "Client", lambda **kw: gw)
    monkeypatch.setattr(edge_service.build, "ensure", lambda cfg, seed: smoke_tb)
    t0 = smoke_tb.periods["test"][0]
    rc = edge_service.run(
        smoke_cfg,
        1,
        spec.station_id,
        "http://testserver",
        reg.root,
        t0,
        t0 + 4 * 3600,
        1e9,
        "validated",
        tmp_path / "st",
    )
    assert rc == 0
    got = sta.load_frame(m.measured(list(spec.sensors)), start)
    assert len(got[got.stream == spec.sensors[0]]) == 16
    stats = sta.load_frame({"U": m.datastreams["UPLINK-STATS"]}, start)
    assert len(stats) >= 4
    assert (tmp_path / "st" / f"{spec.station_id}.sqlite").exists()


def test_mqtt_handler_error_does_not_stop_delivery() -> None:
    from types import SimpleNamespace

    from flowmine.sta.mqtt import StaMqtt, topic

    mq = StaMqtt()
    got: list[tuple[int, dict]] = []

    def broken(ds: int, body: dict) -> None:
        raise KeyError("result")

    mq.handlers = [broken, lambda ds, body: got.append((ds, body))]
    msg = SimpleNamespace(topic=topic(4), payload=b'{"result": 1.5}')
    mq._on_message(mq.client, None, msg)  # type: ignore[arg-type]
    mq._on_message(mq.client, None, msg)  # type: ignore[arg-type]
    assert mq.handler_errors == 2 and got == [(4, {"result": 1.5})] * 2
