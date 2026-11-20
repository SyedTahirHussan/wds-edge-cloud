"""In-memory SensorThings server implementing the subset this package's client uses.

It exists so that the client, the provisioning, the gateway and the central loader
can be tested in milliseconds; the same contract tests also run against a real
FROST-Server. Supported: create (POST) with Locations deep-inserted into a Thing and
links by ``@iot.id``; PATCH; GET of collections, single entities and navigation
paths; ``$filter``, ``$orderby``, ``$top``, ``$skip``, ``$count``, ``$select``,
``$expand`` (one level, with nested options); ``@iot.nextLink`` paging; and the JSON
``$batch`` format of FROST with ``$id`` back-references.
"""

from __future__ import annotations

import copy
import json
import re
import threading
from typing import Any
from urllib.parse import parse_qsl, quote, urlsplit

from fastapi import FastAPI, Request, Response

from flowmine.sta import odata

ENTITY_SETS = (
    "Things",
    "Locations",
    "Sensors",
    "ObservedProperties",
    "Datastreams",
    "Observations",
    "FeaturesOfInterest",
    "HistoricalLocations",
)
# (entity set, navigation property) -> (target set, foreign key on target or source, many?)
SINGLE_NAV = {
    ("Datastreams", "Thing"): ("Things", "Thing"),
    ("Datastreams", "Sensor"): ("Sensors", "Sensor"),
    ("Datastreams", "ObservedProperty"): ("ObservedProperties", "ObservedProperty"),
    ("Observations", "Datastream"): ("Datastreams", "Datastream"),
    ("Observations", "FeatureOfInterest"): ("FeaturesOfInterest", "FeatureOfInterest"),
}
MANY_NAV = {
    ("Things", "Datastreams"): ("Datastreams", "Thing"),
    ("Sensors", "Datastreams"): ("Datastreams", "Sensor"),
    ("ObservedProperties", "Datastreams"): ("Datastreams", "ObservedProperty"),
    ("Datastreams", "Observations"): ("Observations", "Datastream"),
    ("FeaturesOfInterest", "Observations"): ("Observations", "FeatureOfInterest"),
}
SEGMENT = re.compile(r"^(?P<name>[A-Za-z]+)(?:\((?P<id>[^)]+)\))?$")
DEFAULT_TOP = 100
MAX_TOP = 10000


class StaError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class Store:
    """Entities as dicts; links are kept as ``@iot.id`` values under the nav name."""

    def __init__(self) -> None:
        self.sets: dict[str, dict[int, dict[str, Any]]] = {s: {} for s in ENTITY_SETS}
        self.thing_locations: dict[int, list[int]] = {}
        self.next_id = 1
        self.lock = threading.Lock()

    def new_id(self) -> int:
        i = self.next_id
        self.next_id += 1
        return i

    def get(self, es: str, eid: int) -> dict[str, Any]:
        try:
            return self.sets[es][eid]
        except KeyError as exc:
            raise StaError(404, f"{es}({eid}) not found") from exc

    def _ref(self, value: Any, es: str) -> int:
        if isinstance(value, dict) and "@iot.id" in value:
            eid = int(value["@iot.id"])
            self.get(es, eid)
            return eid
        if isinstance(value, dict):
            return self.create(es, value)
        raise StaError(400, f"bad reference to {es}")

    def create(self, es: str, body: dict[str, Any], parent: tuple[str, int] | None = None) -> int:
        body = copy.deepcopy(body)
        eid = self.new_id()
        ent: dict[str, Any] = {}
        if es == "Things":
            locs = [self._ref(v, "Locations") for v in body.pop("Locations", [])]
            self.thing_locations[eid] = locs
            for ds in body.pop("Datastreams", []):
                ds["Thing"] = {"@iot.id": eid}
                self.create("Datastreams", ds)
        if es == "Datastreams":
            if parent is not None and parent[0] == "Things":
                body["Thing"] = {"@iot.id": parent[1]}
            for nav in ("Thing", "Sensor", "ObservedProperty"):
                if nav not in body:
                    raise StaError(400, f"Datastream needs {nav}")
                ent[nav] = self._ref(body.pop(nav), SINGLE_NAV[("Datastreams", nav)][0])
        if es == "Observations":
            if parent is not None and parent[0] == "Datastreams":
                body["Datastream"] = {"@iot.id": parent[1]}
            if "Datastream" not in body:
                raise StaError(400, "Observation needs a Datastream")
            ent["Datastream"] = self._ref(body.pop("Datastream"), "Datastreams")
            foi = body.pop("FeatureOfInterest", None)
            if foi is not None:  # FROST derives one from the Thing's Location otherwise
                ent["FeatureOfInterest"] = self._ref(foi, "FeaturesOfInterest")
            body.setdefault("resultTime", None)
            if "phenomenonTime" not in body:
                raise StaError(400, "Observation needs phenomenonTime in this fake server")
        if (
            es
            in (
                "Things",
                "Locations",
                "Sensors",
                "ObservedProperties",
                "Datastreams",
                "FeaturesOfInterest",
            )
            and "name" not in body
        ):
            raise StaError(400, f"{es} needs a name")
        ent.update(body)
        ent["@iot.id"] = eid
        self.sets[es][eid] = ent
        return eid

    def patch(self, es: str, eid: int, body: dict[str, Any]) -> None:
        ent = self.get(es, eid)
        for k, v in body.items():
            if (es, k) in SINGLE_NAV:
                ent[k] = self._ref(v, SINGLE_NAV[(es, k)][0])
            else:
                ent[k] = v

    def related(self, es: str, ent: dict[str, Any], nav: str) -> tuple[str, list[dict[str, Any]]]:
        eid = ent["@iot.id"]
        if (es, nav) in SINGLE_NAV:
            target, key = SINGLE_NAV[(es, nav)]
            return target, [self.get(target, ent[key])] if key in ent else []
        if (es, nav) in MANY_NAV:
            target, key = MANY_NAV[(es, nav)]
            return target, [e for e in self.sets[target].values() if e.get(key) == eid]
        if es == "Things" and nav == "Locations":
            return "Locations", [
                self.get("Locations", i) for i in self.thing_locations.get(eid, [])
            ]
        if es == "Locations" and nav == "Things":
            return "Things", [
                self.get("Things", t) for t, ls in self.thing_locations.items() if eid in ls
            ]
        raise StaError(400, f"no navigation {es}/{nav}")


class FakeSta:
    def __init__(self, root: str = "http://localhost/v1.1") -> None:
        self.store = Store()
        self.root = root.rstrip("/")

    def resolve(self, es: str) -> odata.Resolver:
        def res(ent: dict[str, Any], path: list[str]) -> Any:
            cur_es = es
            cur: Any = ent
            for seg in path:
                if isinstance(cur, dict) and (cur_es, seg) in SINGLE_NAV:
                    target, key = SINGLE_NAV[(cur_es, seg)]
                    cur, cur_es = self.store.get(target, cur[key]), target
                elif isinstance(cur, dict):
                    cur = cur.get(seg)
                else:
                    return None
            return cur

        return res

    def render(
        self,
        es: str,
        ent: dict[str, Any],
        select: list[str] | None,
        expand: dict[str, dict[str, str]],
    ) -> dict[str, Any]:
        eid = ent["@iot.id"]
        out: dict[str, Any] = {"@iot.selfLink": f"{self.root}/{es}({eid})"}
        for k, v in ent.items():
            if (es, k) in SINGLE_NAV:
                continue
            out[k] = v
        for nav in self._navs(es):
            out[f"{nav}@iot.navigationLink"] = f"{self.root}/{es}({eid})/{nav}"
        for nav, opts in expand.items():
            target, items = self.store.related(es, ent, nav)
            page = self.query(target, items, opts, f"{es}({eid})/{nav}")
            single = (es, nav) in SINGLE_NAV
            out[nav] = page["value"][0] if single and page["value"] else page["value"]
        if select:
            keep = set(select) | set(expand)
            out = {k: v for k, v in out.items() if k in keep}
        return out

    @staticmethod
    def _navs(es: str) -> list[str]:
        navs = [n for (s, n) in SINGLE_NAV if s == es] + [n for (s, n) in MANY_NAV if s == es]
        if es == "Things":
            navs.append("Locations")
        if es == "Locations":
            navs.append("Things")
        return navs

    def query(
        self, es: str, items: list[dict[str, Any]], params: dict[str, str], path: str
    ) -> dict[str, Any]:
        if "$filter" in params:
            tree = odata.Parser(params["$filter"]).parse()
            res = self.resolve(es)
            items = [e for e in items if odata.evaluate(tree, e, res)]
        res = self.resolve(es)
        keys = odata.order_key(params.get("$orderby", "@iot.id"))
        for p, desc in reversed(keys):

            def key(e: dict[str, Any], p: list[str] = p) -> tuple[int, Any]:
                return _sort_key(odata.parse_time(res(e, p)))

            items = sorted(items, key=key, reverse=desc)
        top = min(int(params.get("$top", DEFAULT_TOP)), MAX_TOP)
        skip = int(params.get("$skip", 0))
        page = items[skip : skip + top]
        select = [s.strip() for s in params["$select"].split(",")] if "$select" in params else None
        expand = odata.parse_expand(params["$expand"]) if "$expand" in params else {}
        out: dict[str, Any] = {}
        if params.get("$count") == "true":
            out["@iot.count"] = len(items)
        out["value"] = [self.render(es, e, select, expand) for e in page]
        if skip + top < len(items):
            nxt = dict(params, **{"$skip": str(skip + top), "$top": str(top)})
            qs = "&".join(f"{k}={quote(v, safe='')}" for k, v in nxt.items())
            out["@iot.nextLink"] = f"{self.root}/{path}?{qs}"
        return out

    def handle(
        self, method: str, path: str, params: dict[str, str], body: Any
    ) -> tuple[int, Any, str | None]:
        """Return (status, json body or None, Location header)."""
        path = path.strip("/")
        if path == "":
            return (
                200,
                {
                    "value": [{"name": s, "url": f"{self.root}/{s}"} for s in ENTITY_SETS],
                    "serverSettings": {"conformance": []},
                },
                None,
            )
        if path == "$batch" and method == "POST":
            return 200, self.batch(body), None
        segs = [SEGMENT.match(s) for s in path.split("/")]
        if any(m is None for m in segs):
            raise StaError(400, f"bad path {path}")
        first = segs[0]
        assert first is not None
        es = first.group("name")
        if es not in ENTITY_SETS:
            raise StaError(404, f"unknown entity set {es}")
        ent: dict[str, Any] | None = None
        items: list[dict[str, Any]] | None = None
        parent: tuple[str, int] | None = None
        if first.group("id"):
            ent = self.store.get(es, int(first.group("id")))
        for m in segs[1:]:
            assert m is not None
            nav = m.group("name")
            if ent is None:
                raise StaError(400, "navigation needs an entity")
            parent = (es, ent["@iot.id"])
            target, related = self.store.related(es, ent, nav)
            es = target
            if m.group("id"):
                ent = next((e for e in related if e["@iot.id"] == int(m.group("id"))), None)
                if ent is None:
                    raise StaError(404, "not found")
            elif (parent[0], nav) in SINGLE_NAV:
                ent = related[0]
            else:
                ent, items = None, related
        with self.store.lock:
            if method == "GET":
                if ent is not None and items is None:
                    sel = (
                        [s.strip() for s in params["$select"].split(",")]
                        if "$select" in params
                        else None
                    )
                    exp = odata.parse_expand(params["$expand"]) if "$expand" in params else {}
                    return 200, self.render(es, ent, sel, exp), None
                all_items = items if items is not None else list(self.store.sets[es].values())
                return 200, self.query(es, all_items, params, path), None
            if method == "POST":
                eid = self.store.create(es, body, parent if items is not None else None)
                loc = f"{self.root}/{es}({eid})"
                return 201, self.render(es, self.store.get(es, eid), None, {}), loc
            if method == "PATCH" and ent is not None:
                self.store.patch(es, ent["@iot.id"], body)
                return 200, self.render(es, ent, None, {}), None
            if method == "DELETE" and ent is not None:
                del self.store.sets[es][ent["@iot.id"]]
                return 200, None, None
        raise StaError(405, f"{method} not supported on {path}")

    def batch(self, body: dict[str, Any]) -> dict[str, Any]:
        created: dict[str, str] = {}
        responses = []
        for req in body.get("requests", []):
            url = req["url"]
            payload = req.get("body")
            text = json.dumps(payload) if payload is not None else None
            for ref, val in created.items():
                url = url.replace(f"${ref}", val)
                if text is not None:
                    text = text.replace(f'"${ref}"', val)
            parts = urlsplit(url)
            path = parts.path
            if path.startswith(self.root):
                path = path[len(self.root) :]
            params = dict(parse_qsl(parts.query))
            try:
                status, out, loc = self.handle(
                    req["method"].upper(), path, params, json.loads(text) if text else None
                )
                item: dict[str, Any] = {"id": req.get("id"), "status": status}
                if loc is not None:
                    item["location"] = loc
                    created[str(req.get("id"))] = loc.rsplit("(", 1)[1].rstrip(")")
                elif out is not None:
                    item["body"] = out
            except StaError as exc:
                item = {"id": req.get("id"), "status": exc.status, "body": {"message": str(exc)}}
            responses.append(item)
        return {"responses": responses}


def _sort_key(v: Any) -> tuple[int, Any]:
    if v is None:
        return (0, 0)
    if isinstance(v, (int, float)):
        return (1, v)
    return (2, str(v)) if not hasattr(v, "timestamp") else (1, v.timestamp())


def create_app(root_path: str = "/v1.1", base_url: str = "http://localhost") -> FastAPI:
    """FastAPI app serving a FakeSta under ``root_path``."""
    fake = FakeSta(base_url.rstrip("/") + root_path)
    app = FastAPI(title="flowmine fake SensorThings server")
    app.state.fake = fake

    @app.get(root_path)
    async def service_root() -> Response:
        _, out, _ = fake.handle("GET", "", {}, None)
        return Response(json.dumps(out), media_type="application/json")

    @app.api_route(root_path + "/{path:path}", methods=["GET", "POST", "PATCH", "DELETE"])
    async def any_path(path: str, request: Request) -> Response:
        raw = await request.body()
        body = json.loads(raw) if raw else None
        params = dict(request.query_params)
        try:
            status, out, loc = fake.handle(request.method, path, params, body)
        except StaError as exc:
            return Response(
                json.dumps({"code": exc.status, "message": str(exc)}),
                status_code=exc.status,
                media_type="application/json",
            )
        except ValueError as exc:
            return Response(
                json.dumps({"code": 400, "message": str(exc)}),
                status_code=400,
                media_type="application/json",
            )
        headers = {"Location": loc} if loc else {}
        content = json.dumps(out) if out is not None else ""
        return Response(content, status_code=status, media_type="application/json", headers=headers)

    return app
