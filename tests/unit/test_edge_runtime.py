from __future__ import annotations

import httpx
import numpy as np
import pytest

from flowmine.edge import replay
from flowmine.edge.core import codec, quality
from flowmine.edge.node import MemoryLink, model_package
from flowmine.edge.store import LocalStore
from flowmine.testbed.build import Testbed


def _station(tb: Testbed, cls: str):
    return next(s for s in tb.stations if s.station_class == cls)


def test_store_retention_history_and_outbox() -> None:
    st = LocalStore(retention_s=1000)
    st.put([("P-1", t, 1.0 * t, 1.0 * t, 0, 0.0) for t in range(0, 3000, 300)])
    assert [r[0] for r in st.history("P-1", 600, 1500)] == [600, 900, 1200]
    assert st.purge(3000) == 7 and st.count() == 3
    st.enqueue(1, b"a")
    st.enqueue(2, b"b")
    assert [f for _, _, f in st.outbox()] == [b"a", b"b"]
    st.ack(st.outbox()[0][0])
    assert [f for _, _, f in st.outbox()] == [b"b"]


def test_node_validates_every_slot_and_flags_faults(smoke_tb: Testbed, smoke_bundles) -> None:
    t0, t1 = smoke_tb.periods["calib"][0], smoke_tb.periods["test"][1]
    lab = smoke_tb.labels
    recs = []
    for spec in smoke_tb.stations:
        node = replay.run_station(
            replay.make_node(smoke_tb, spec, smoke_bundles, "validated"), smoke_tb, t0, t1
        )
        recs.extend(node.records)
        assert node.store.count() == len(node.records)
    got = {(r[0], r[1]): r[4] for r in recs}
    for s in smoke_tb.sensors:
        times = [r[1] for r in recs if r[0] == s.sensor_id]
        assert sorted(times) == list(range(t0, t1, s.period_s))
    gaps = lab[lab.fault_type.isin(["gap_short", "gap_long"])]
    assert all(
        got[(a, t)] & quality.MISSING for a, t in zip(gaps.sensor_id, gaps.time_s, strict=True)
    )
    spikes = lab[lab.fault_type == "spike"]
    assert all(
        got[(a, t)] & quality.REJECTING
        for a, t in zip(spikes.sensor_id, spikes.time_s, strict=True)
    )
    stuck = lab[lab.fault_type == "stuck"]
    hit = [
        bool(got[(a, t)] & quality.FLATLINE)
        for a, t in zip(stuck.sensor_id, stuck.time_s, strict=True)
    ]
    assert np.mean(hit) > 0.5


def test_policy_byte_ordering(smoke_tb: Testbed, smoke_bundles) -> None:
    t0, t1 = smoke_tb.periods["calib"][0], smoke_tb.periods["test"][1]
    spec = _station(smoke_tb, "pump")
    stats = {}
    for policy in ("raw", "validated", "delta", "summary"):
        node = replay.run_station(
            replay.make_node(smoke_tb, spec, smoke_bundles, policy), smoke_tb, t0, t1
        )
        stats[policy] = node.policy.stats
        assert all(len(f) <= smoke_tb.cfg.edge.max_payload for _, f in node.link.frames)
        assert sum(len(f) for _, f in node.link.frames) == node.policy.stats.binary_bytes
    assert stats["delta"].binary_bytes < stats["validated"].binary_bytes
    assert stats["summary"].binary_bytes < stats["validated"].binary_bytes
    for s in stats.values():
        assert s.json_bytes > 5 * s.binary_bytes


def test_delta_policy_cloud_error_bounded(smoke_tb: Testbed, smoke_bundles) -> None:
    """Rebuild the cloud signal from delta frames; between transmissions it stays within eps."""
    from flowmine.edge.core.dualpred import DeltaReceiver

    t0, t1 = smoke_tb.periods["test"]
    spec = _station(smoke_tb, "logger")
    node = replay.run_station(
        replay.make_node(smoke_tb, spec, smoke_bundles, "delta"), smoke_tb, t0, t1
    )
    sid = spec.sensors[0]
    s = smoke_tb.sensor(sid)
    scales = {x.local_id: x.resolution for x in (smoke_tb.sensor(i) for i in spec.sensors)}
    got = {}
    for _, fr in node.link.frames:
        for rec in codec.decode(fr, scales)[2]:
            if rec[0] == s.local_id:
                got[rec[1]] = rec[2]
    rx = DeltaReceiver(smoke_bundles[sid].hw_copy())
    eps = smoke_tb.cfg.sensors[s.kind].epsilon
    worst = 0.0
    for r in (r for r in node.records if r[0] == sid):
        est = rx.step(r[1], got.get(r[1]))
        if r[3] is not None:
            worst = max(worst, abs(est - r[3]))
    assert worst <= eps + s.resolution
    assert len(got) < 0.8 * len([r for r in node.records if r[0] == sid])


def test_store_and_forward_replays_in_order(smoke_tb: Testbed, smoke_bundles) -> None:
    t0, t1 = smoke_tb.periods["test"]
    spec = _station(smoke_tb, "logger")
    mid0, mid1 = t0 + 6 * 3600, t0 + 18 * 3600
    link = MemoryLink()
    node = replay.make_node(smoke_tb, spec, smoke_bundles, "validated", link=link)
    seen_backlog = []

    def up(t: int) -> bool:
        if mid0 <= t < mid1:
            seen_backlog.append(node.backlog())
            return False
        return True

    replay.run_station(node, smoke_tb, t0, t1, link_up=up)
    assert max(seen_backlog) > 10 and node.backlog() == 0
    ref = replay.run_station(
        replay.make_node(smoke_tb, spec, smoke_bundles, "validated"), smoke_tb, t0, t1
    )
    # Same frames in the same order as without the outage: nothing lost, nothing reordered.
    assert [f for _, f in ref.link.frames] == [f for _, f in link.frames]


def test_model_install_hash_check_and_rollback(smoke_tb: Testbed, smoke_bundles) -> None:
    spec = _station(smoke_tb, "logger")
    sid = spec.sensors[0]
    t0 = smoke_tb.periods["calib"][0]
    node = replay.run_station(
        replay.make_node(smoke_tb, spec, smoke_bundles, "validated"), smoke_tb, t0, t0 + 86400
    )
    blob, digest = model_package(sid, 2, smoke_bundles[sid].hw_copy())
    assert not node.install_model(blob, "0" * 64)
    assert node.model_version(sid) == 1
    assert node.install_model(blob, digest)
    assert node.model_version(sid) == 2
    model = node.current[sid].model
    assert model.k == (t0 + 86400) // smoke_tb.sensor(sid).period_s - 1
    rec = node.ingest(sid, t0 + 86400, model.forecast_at(t0 + 86400))[0]
    assert quality.model_version(rec[4]) == 2
    assert node.rollback(sid) and node.model_version(sid) == 1
    assert not node.rollback(sid)


@pytest.mark.parametrize("forecaster", ["hw", "mlp", "snaive"])
def test_all_forecasters_run_in_the_node(smoke_tb: Testbed, smoke_bundles, forecaster: str) -> None:
    t0 = smoke_tb.periods["test"][0]
    spec = _station(smoke_tb, "tank")
    node = replay.run_station(
        replay.make_node(smoke_tb, spec, smoke_bundles, "validated", forecaster=forecaster),
        smoke_tb,
        t0,
        t0 + 86400,
    )
    vals = np.array([r[3] for r in node.records if r[3] is not None])
    assert len(vals) > 100 and np.isfinite(vals).all()
    # After the day, each sensor's forecasts for the next hour beat the day's mean.
    t1 = t0 + 86400
    truth = smoke_tb.truth
    for sid in spec.sensors:
        p = smoke_tb.sensor(sid).period_s
        tr = truth[truth.sensor_id == sid].set_index("time_s").value
        ahead = [t1 + h * p for h in range(12)]
        model = node.current[sid].model
        err = np.mean([abs(model.forecast_at(t) - tr[t]) for t in ahead])
        const = np.mean([abs(tr.loc[t0 : t1 - 1].mean() - tr[t]) for t in ahead])
        assert err < const, (sid, err, const)


def test_http_link_drops_rejected_frames_and_retries_server_errors() -> None:
    from flowmine.edge.service import HttpLink

    answers = iter([400, 503, 200])
    seen: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.content)
        return httpx.Response(next(answers), text="x")

    link = HttpLink("http://gw")
    link.http = httpx.Client(transport=httpx.MockTransport(handler))
    link.send("ST-1", b"bad")
    assert link.rejected == 1 and link.up
    with pytest.raises(ConnectionError):
        link.send("ST-1", b"good")
    assert not link.up
    link.send("ST-1", b"good")
    assert link.up and seen == [b"bad", b"good", b"good"]


def test_model_install_gives_the_delta_sender_its_own_copy(
    smoke_tb: Testbed, smoke_bundles
) -> None:
    spec = _station(smoke_tb, "logger")
    sid = spec.sensors[0]
    t0 = smoke_tb.periods["calib"][0]
    node = replay.run_station(
        replay.make_node(smoke_tb, spec, smoke_bundles, "delta"), smoke_tb, t0, t0 + 86400
    )
    blob, digest = model_package(sid, 2, smoke_bundles[sid].hw_copy())
    assert node.install_model(blob, digest)
    sender = node.policy.senders[node.local[sid]]
    active = node.current[sid].model
    assert sender.model is not active and sender.model.state() == active.state()
    assert sender.last_tx is None
