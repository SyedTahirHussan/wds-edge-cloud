"""One suite, two servers: the fake in-memory SensorThings server and FROST-Server."""

from __future__ import annotations

import uuid
from datetime import datetime

import numpy as np
import pytest

from flowmine.config import Config
from flowmine.edge import replay
from flowmine.edge.core import quality
from flowmine.sta import mapping
from flowmine.sta.client import StaClient, StaHttpError
from flowmine.sta.gateway import Gateway, expand_version
from flowmine.sta.provision import load_map, provision
from flowmine.testbed.build import Testbed

START = datetime.fromisoformat("2025-06-02T00:00:00+00:00")


def _stream(sta: StaClient, kind: str = "pressure") -> int:
    """A fresh Datastream (unique name) so tests on a persistent server do not interfere."""
    tag = uuid.uuid4().hex[:8]
    sensor_key = {"maintenance": "maintenance_log", "event": "analytics"}.get(kind, kind)
    sid = sta.ensure("Sensors", mapping.sensor_body(sensor_key))
    op = sta.ensure("ObservedProperties", mapping.observed_property_body(kind))
    tid = sta.create(
        "Things",
        {
            "name": f"T-{tag}",
            "description": "contract test",
            "properties": {"station_class": "test", "tag": tag},
            "Locations": [mapping.location_body("test", 1.0, 2.0)],
        },
    )
    return sta.ensure(
        "Datastreams",
        mapping.datastream_body(f"DS-{tag}", kind, "measured", tid, sid, op, {"tag": tag}),
    )


def test_provisioning_is_idempotent_and_maps_epanet(
    sta: StaClient, smoke_cfg: Config, smoke_tb: Testbed
) -> None:
    m1 = provision(sta, smoke_cfg, smoke_tb.sensors, smoke_tb.stations, ["101", "123"])
    n_things = len(list(sta.iterate("Things", {"$top": 50})))
    m2 = provision(sta, smoke_cfg, smoke_tb.sensors, smoke_tb.stations, ["101", "123"])
    assert m1.datastreams == m2.datastreams and m1.things == m2.things
    assert len(list(sta.iterate("Things", {"$top": 50}))) == n_things
    assert load_map(sta).datastreams.items() >= m1.datastreams.items()
    tank = next(s for s in smoke_tb.stations if s.station_class == "tank")
    thing = sta.get(f"Things({m1.things[tank.station_id]})", {"$expand": "Locations,Datastreams"})
    props = thing["properties"]
    assert props["station_class"] == "tank" and props["dma"].startswith("DMA-")
    assert {s["epanet_element"] for s in props["sensors"]} == {
        smoke_tb.sensor(s).element for s in tank.sensors
    }
    assert thing["Locations"][0]["location"]["type"] == "Point"
    assert {d["name"] for d in thing["Datastreams"]} >= set(tank.sensors)
    tanks = sta.get("Things", {"$filter": "properties/station_class eq 'tank'"})["value"]
    assert {t["name"] for t in tanks} >= {
        s.station_id for s in smoke_tb.stations if s.station_class == "tank"
    }
    ds = sta.get(
        f"Datastreams({m1.datastreams['LAB-' + next(s.sensor_id for s in smoke_tb.sensors if s.kind == 'chlorine')]})",
        {"$expand": "Sensor,ObservedProperty"},
    )
    assert ds["Sensor"]["name"] == "laboratory method"
    assert ds["unitOfMeasurement"]["symbol"] == "mg/L"


def test_bulk_post_query_options_and_paging(sta: StaClient) -> None:
    ds = _stream(sta)
    bodies = [
        mapping.observation_body(
            START, i * 300, 40.0 + i * 0.01, quality.make(0, 3), 3, datastream_id=ds
        )
        for i in range(250)
    ]
    assert sta.post_observations(bodies) == 250
    obs = list(sta.observations(ds))
    assert len(obs) == 250
    assert [o["result"] for o in obs[:3]] == pytest.approx([40.0, 40.01, 40.02])
    t0, t1 = mapping.iso(START, 300 * 100), mapping.iso(START, 300 * 110)
    sub = list(sta.observations(ds, t0, t1, page=4))
    assert len(sub) == 10 and sub[0]["phenomenonTime"].startswith(t0[:19])
    page = sta.get(
        f"Datastreams({ds})/Observations",
        {"$top": 5, "$orderby": "phenomenonTime desc", "$select": "result"},
    )
    assert set(page["value"][0]) <= {"result", "@iot.selfLink", "@iot.id"}
    assert page["value"][0]["result"] == pytest.approx(40.0 + 249 * 0.01)
    assert "@iot.nextLink" in page
    flt = sta.get("Observations", {"$filter": f"Datastream/@iot.id eq {ds} and result gt 42.4"})
    assert len(flt["value"]) == 9


def test_quality_model_version_and_lab_delay_roundtrip(sta: StaClient) -> None:
    ds = _stream(sta, "chlorine")
    code = quality.make(quality.MODEL_RESIDUAL | quality.RECONSTRUCTED, 7)
    b1 = mapping.observation_body(START, 900, 0.42, code, 7, raw=0.9, datastream_id=ds)
    b2 = mapping.observation_body(START, 1800, 0.40, datastream_id=ds, result_t_s=1800 + 30 * 3600)
    sta.post_observations([b1, b2])
    df = sta.load_frame({"X": ds}, START)
    assert list(df.time_s) == [900, 1800]
    r1, r2 = df.iloc[0], df.iloc[1]
    assert r1.quality == code and r1.model_version == 7 and bool(r1.reconstructed)
    assert r2.result_s - r2.time_s == 30 * 3600
    full = list(sta.observations(ds))[0]
    assert full["parameters"]["raw"] == pytest.approx(0.9)
    assert full["resultQuality"]["flags"] == ["MODEL_RESIDUAL", "RECONSTRUCTED"]


def test_maintenance_record_as_json_result(sta: StaClient) -> None:
    ds = _stream(sta, "maintenance")
    rec = {"type": "valve_operation", "asset": "169", "note": "isolation valve closed, 3.2 h"}
    sta.post_observations([mapping.observation_body(START, 3600, rec, datastream_id=ds)])
    got = list(sta.observations(ds))[0]["result"]
    assert got == rec


def test_gateway_frames_to_observations(
    sta: StaClient, smoke_tb: Testbed, smoke_bundles, smoke_cfg: Config
) -> None:
    m = provision(sta, smoke_cfg, smoke_tb.sensors, smoke_tb.stations)
    tag = uuid.uuid4().hex[:6]
    ds_ids = {}
    for s in smoke_tb.sensors:
        ds_ids[s.sensor_id] = sta.ensure(
            "Datastreams",
            mapping.datastream_body(
                f"GW-{tag}-{s.sensor_id}",
                s.kind,
                "measured",
                m.things[s.station],
                m.sensors[s.kind],
                m.observed_properties[s.kind],
                {},
            ),
        )
    spec = next(s for s in smoke_tb.stations if s.station_class == "tank")
    t0 = smoke_tb.periods["test"][0]
    node = replay.run_station(
        replay.make_node(smoke_tb, spec, smoke_bundles, "validated"), smoke_tb, t0, t0 + 6 * 3600
    )
    gw = Gateway(smoke_tb.stations, smoke_tb.sensors, ds_ids, START, deployed=lambda st: 9)
    bodies = [b for _, fr in node.link.frames for b in gw.decode(fr)]
    sta.post_observations(bodies)
    df = sta.load_frame({s: ds_ids[s] for s in spec.sensors}, START)
    recs = [r for r in node.records if r[3] is not None]
    assert len(df) == len(recs)
    edge = {(r[0], r[1]): r[3] for r in recs}
    err = [abs(v - edge[(s, t)]) for s, t, v in zip(df.stream, df.time_s, df.value, strict=True)]
    assert max(err) <= max(smoke_tb.sensor(s).resolution for s in spec.sensors) / 2 + 1e-9
    assert set(df.model_version) == {9}
    assert expand_version(9 % 8, 9) == 9 and expand_version(0, 9) == 8


def test_errors_surface(sta: StaClient) -> None:
    with pytest.raises(StaHttpError):
        sta.get("Datastreams(999999999)")
    assert np.isfinite(
        sta.create(
            "Sensors", {**mapping.sensor_body("pressure"), "name": f"S-{uuid.uuid4().hex[:6]}"}
        )
    )
