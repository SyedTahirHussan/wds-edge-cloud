"""``flowmine dashboard``: one local page showing what SensorThings holds.

The backend reads only from the SensorThings server (and the .inp for the drawing);
the page polls it every few seconds. No external CDN: one HTML file with vanilla JS
and SVG.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse

from flowmine.edge.core import quality
from flowmine.sta.client import StaClient
from flowmine.testbed import network

STATIC = Path(__file__).parent / "static"
LATEST = (
    "Observations($top=1;$orderby=phenomenonTime desc;$select=phenomenonTime,result,resultQuality)"
)


def state_of(code: int | None) -> str:
    if code is None:
        return "none"
    if code & (quality.REJECTING | quality.CROSSCHECK):
        return "flagged"
    if code & quality.MISSING:
        return "missing"
    if code & quality.RECONSTRUCTED:
        return "reconstructed"
    if code & quality.SHIFT:
        return "shift"
    return "ok"


def create_app(client: StaClient, inp: str | None) -> FastAPI:
    app = FastAPI(title="flowmine dashboard")
    wn = network.load_network(inp)
    xy = network.coordinates(wn)
    links = [[lk.start_node_name, lk.end_node_name] for _, lk in wn.links()]

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/api/network")
    def net() -> dict[str, Any]:
        params = {"$top": 1000, "$expand": f"Datastreams($expand={LATEST})"}
        things = list(client.iterate("Things", params))
        stations = []
        for t in things:
            props = t.get("properties") or {}
            node = props.get("epanet_node")
            if node not in xy:
                continue
            worst, latest = "none", None
            for ds in t.get("Datastreams", []):
                if (ds.get("properties") or {}).get("role") != "measured":
                    continue
                for o in ds.get("Observations", []):
                    q = (o.get("resultQuality") or {}).get("code")
                    st = state_of(q)
                    rank = ["none", "ok", "shift", "reconstructed", "missing", "flagged"]
                    if rank.index(st) > rank.index(worst):
                        worst = st
                    latest = max(latest or o["phenomenonTime"], o["phenomenonTime"])
            stations.append(
                {
                    "id": t["name"],
                    "node": node,
                    "x": xy[node][0],
                    "y": xy[node][1],
                    "class": props.get("station_class"),
                    "state": worst,
                    "latest": latest,
                    "sensors": [s["sensor_id"] for s in props.get("sensors", [])],
                }
            )
        return {"nodes": xy, "links": links, "stations": stations}

    def stream(name: str, n: int) -> dict[str, list[Any]]:
        found = client.find_by_name("Datastreams", name)
        if found is None:
            return {"t": [], "v": [], "q": [], "raw": []}
        page = client.get(
            f"Datastreams({found['@iot.id']})/Observations",
            {
                "$top": n,
                "$orderby": "phenomenonTime desc",
                "$select": "phenomenonTime,result,resultQuality,parameters",
            },
        )
        obs = list(reversed(page.get("value", [])))
        return {
            "t": [o["phenomenonTime"].split("/")[0] for o in obs],
            "v": [o["result"] for o in obs],
            "q": [(o.get("resultQuality") or {}).get("code", 0) for o in obs],
            "raw": [(o.get("parameters") or {}).get("raw", o["result"]) for o in obs],
        }

    @app.get("/api/series/{sensor}")
    def series(sensor: str, n: int = 576) -> dict[str, Any]:
        return {
            "sensor": sensor,
            "measured": stream(sensor, n),
            "simulated": stream(f"SIM-{sensor}", n),
            "residual": stream(f"RES-{sensor}", n),
        }

    @app.get("/api/counters")
    def counters() -> dict[str, Any]:
        found = client.find_by_name("Datastreams", "UPLINK-STATS")
        if found is None:
            return {}
        page = client.get(
            f"Datastreams({found['@iot.id']})/Observations",
            {"$top": 500, "$orderby": "phenomenonTime desc"},
        )
        latest: dict[str, dict[str, Any]] = {}
        for o in page.get("value", []):
            r = o["result"]
            latest.setdefault(r["station"], r["policies"])
        total: dict[str, dict[str, float]] = {}
        for pol in latest.values():
            for name, st in pol.items():
                t = total.setdefault(name, {"binary_bytes": 0, "json_bytes": 0, "frames": 0})
                for k in t:
                    t[k] += st.get(k, 0)
        return {"stations": len(latest), "policies": total}

    @app.get("/api/events")
    def events(n: int = 20) -> list[dict[str, Any]]:
        found = client.find_by_name("Datastreams", "EVENTS")
        if found is None:
            return []
        page = client.get(
            f"Datastreams({found['@iot.id']})/Observations",
            {"$top": n, "$orderby": "phenomenonTime desc"},
        )
        return [{"t": o["phenomenonTime"], **o["result"]} for o in page.get("value", [])]

    return app
