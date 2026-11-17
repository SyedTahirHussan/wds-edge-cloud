"""SensorThings API client (HTTP, JSON). Works against FROST-Server and the fake server.

Bulk posting uses FROST's JSON batch request (``POST $batch`` with
``{"requests": [...]}``); the OGC v1.1 standard itself only defines multipart batches,
and FROST documents the JSON form as its extension.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from datetime import datetime
from typing import Any

import httpx
import pandas as pd

from flowmine.sta import mapping

log = logging.getLogger(__name__)


FRAME_COLUMNS = [
    "stream",
    "time_s",
    "result_s",
    "value",
    "quality",
    "model_version",
    "reconstructed",
]


def observation_row(stream: str, o: dict[str, Any], start: datetime) -> dict[str, Any]:
    """One tidy row from an Observation JSON body (as served or as posted)."""
    q = o.get("resultQuality") or {}
    p = o.get("parameters") or {}
    rt = o.get("resultTime")
    return {
        "stream": stream,
        "time_s": mapping.seconds(start, o["phenomenonTime"].split("/")[0]),
        "result_s": mapping.seconds(start, rt) if rt else None,
        "value": o["result"],
        "quality": int(q.get("code", 0)) if isinstance(q, dict) else 0,
        "model_version": p.get("model_version"),
        "reconstructed": bool(p.get("reconstructed", False)),
    }


def frame_from_bodies(
    bodies: list[dict[str, Any]], names: dict[int, str], start: datetime
) -> pd.DataFrame:
    """Tidy frame from posted Observation bodies; ``names`` maps Datastream id to stream."""
    rows = [observation_row(names[int(b["Datastream"]["@iot.id"])], b, start) for b in bodies]
    return pd.DataFrame(rows, columns=FRAME_COLUMNS)


class StaHttpError(RuntimeError):
    pass


def _quote(v: str) -> str:
    return "'" + v.replace("'", "''") + "'"


class StaClient:
    def __init__(
        self,
        base_url: str,
        http: httpx.Client | None = None,
        timeout: float = 30.0,
        batch_size: int = 200,
    ) -> None:
        self.base = base_url.rstrip("/")
        self.http = http or httpx.Client(timeout=timeout)
        self.batch_size = batch_size

    def url(self, path: str) -> str:
        return path if path.startswith("http") else f"{self.base}/{path.lstrip('/')}"

    def _check(self, r: httpx.Response) -> httpx.Response:
        if r.status_code >= 400:
            raise StaHttpError(
                f"{r.request.method} {r.request.url} -> {r.status_code}: {r.text[:300]}"
            )
        return r

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._check(self.http.get(self.url(path), params=params)).json()

    def iterate(self, path: str, params: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
        """Yield every entity of a collection, following ``@iot.nextLink``."""
        page = self.get(path, params)
        while True:
            yield from page.get("value", [])
            nxt = page.get("@iot.nextLink")
            if not nxt:
                return
            page = self.get(nxt)

    def create(self, path: str, body: dict[str, Any]) -> int:
        r = self._check(self.http.post(self.url(path), json=body))
        loc = r.headers.get("location")
        if loc:
            return int(loc.rsplit("(", 1)[1].rstrip(")"))
        return int(r.json()["@iot.id"])

    def patch(self, path: str, body: dict[str, Any]) -> None:
        self._check(self.http.patch(self.url(path), json=body))

    def find(self, entity_set: str, flt: str) -> dict[str, Any] | None:
        page = self.get(entity_set, {"$filter": flt, "$top": 1})
        vals = page.get("value", [])
        return vals[0] if vals else None

    def find_by_name(self, entity_set: str, name: str) -> dict[str, Any] | None:
        return self.find(entity_set, f"name eq {_quote(name)}")

    def ensure(self, entity_set: str, body: dict[str, Any]) -> int:
        """Create the entity unless one with the same name exists (idempotent provisioning)."""
        found = self.find_by_name(entity_set, body["name"])
        if found is not None:
            return int(found["@iot.id"])
        return self.create(entity_set, body)

    def batch(self, requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
        r = self._check(self.http.post(self.url("$batch"), json={"requests": requests}))
        responses: list[dict[str, Any]] = r.json()["responses"]
        bad = [x for x in responses if int(x["status"]) >= 400]
        if bad:
            raise StaHttpError(f"{len(bad)} batch requests failed, first: {bad[0]}")
        return responses

    def post_observations(self, bodies: list[dict[str, Any]]) -> int:
        """Post Observations (each with a Datastream reference) in JSON batches."""
        n = 0
        for i in range(0, len(bodies), self.batch_size):
            chunk = bodies[i : i + self.batch_size]
            reqs = [
                {"id": str(j), "method": "post", "url": "Observations", "body": b}
                for j, b in enumerate(chunk)
            ]
            self.batch(reqs)
            n += len(chunk)
        return n

    def observations(
        self, ds_id: int, t0: str | None = None, t1: str | None = None, page: int = 10000
    ) -> Iterator[dict[str, Any]]:
        flt = []
        if t0:
            flt.append(f"phenomenonTime ge {t0}")
        if t1:
            flt.append(f"phenomenonTime lt {t1}")
        params: dict[str, Any] = {
            "$orderby": "phenomenonTime asc",
            "$top": page,
            "$select": "phenomenonTime,resultTime,result,resultQuality,parameters",
        }
        if flt:
            params["$filter"] = " and ".join(flt)
        return self.iterate(f"Datastreams({ds_id})/Observations", params)

    def load_frame(
        self,
        ds_ids: dict[str, int],
        start: datetime,
        t0_s: int | None = None,
        t1_s: int | None = None,
    ) -> pd.DataFrame:
        """Tidy frame of observations: one row per (stream, phenomenon time).

        Columns: stream, time_s, result_s, value, quality, model_version, reconstructed.
        ``time_s``/``result_s`` are seconds after ``start``.
        """
        t0 = mapping.iso(start, t0_s) if t0_s is not None else None
        t1 = mapping.iso(start, t1_s) if t1_s is not None else None
        rows: list[dict[str, Any]] = []
        for name, ds in ds_ids.items():
            rows.extend(observation_row(name, o, start) for o in self.observations(ds, t0, t1))
        return pd.DataFrame(rows, columns=FRAME_COLUMNS)

    def wait(self, timeout_s: float = 120.0) -> bool:
        """Poll the service root until it answers (FROST needs a while to start)."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                r = self.http.get(self.base, timeout=5.0)
                if r.status_code == 200 and "value" in r.json():
                    return True
            except (httpx.HTTPError, ValueError):
                pass
            time.sleep(2.0)
        return False
