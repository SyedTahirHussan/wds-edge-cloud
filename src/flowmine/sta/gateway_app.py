"""``flowmine gateway``: HTTP endpoint that receives station frames and posts Observations.

POST /uplink/{station} with the binary frame as body; POST /stats/{station} with the
station's byte counters (stored as an observation of the UPLINK-STATS Datastream, so
the dashboard reads them from SensorThings like everything else).
"""

from __future__ import annotations

import logging
import struct
from datetime import datetime

from fastapi import FastAPI, HTTPException, Request

from flowmine.hybrid.registry import ModelRegistry
from flowmine.sta import mapping
from flowmine.sta.client import StaClient
from flowmine.sta.gateway import Gateway
from flowmine.sta.provision import StaMap
from flowmine.testbed.sensors import Sensor, StationSpec

log = logging.getLogger(__name__)


def create_app(
    client: StaClient,
    m: StaMap,
    stations: list[StationSpec],
    sensors: list[Sensor],
    start: datetime,
    registry: ModelRegistry | None = None,
    summary_s: int = 900,
) -> FastAPI:
    by_station = {st.station_id: list(st.sensors) for st in stations}

    def deployed(station: str) -> int:
        return registry.deployed(by_station[station]) if registry is not None else 1

    ds = m.measured([s.sensor_id for s in sensors])
    gw = Gateway(stations, sensors, ds, start, deployed, summary_s)
    app = FastAPI(title="flowmine gateway")
    app.state.gateway = gw

    @app.post("/uplink/{station}")
    async def uplink(station: str, request: Request) -> dict[str, int]:
        frame = await request.body()
        if station not in by_station:
            raise HTTPException(404, f"unknown station {station}")
        try:
            bodies = gw.decode(frame)
        except (ValueError, KeyError, IndexError, struct.error) as exc:
            raise HTTPException(400, f"bad frame: {exc}") from exc
        client.post_observations(bodies)
        return {"observations": len(bodies)}

    @app.post("/stats/{station}")
    async def stats(station: str, request: Request) -> dict[str, str]:
        body = await request.json()
        ds = m.datastreams["UPLINK-STATS"]
        obs = mapping.observation_body(
            start,
            int(body["t"]),
            {"station": station, "policies": body["policies"]},
            datastream_id=ds,
        )
        client.post_observations([obs])
        return {"ok": "stored"}

    @app.get("/health")
    async def health() -> dict[str, int]:
        return {"frames": gw.frames, "bytes": gw.bytes}

    return app
