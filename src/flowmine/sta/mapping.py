"""Mapping of testbed entities to OGC SensorThings API v1.1 entities (JSON bodies).

The choices are documented in docs/sensorthings-mapping.md. In short: a station is a
Thing whose ``properties`` carry the EPANET element of each of its sensors; measured,
laboratory and simulated values use separate Sensor entities; ``resultQuality``
carries the edge quality code and ``parameters`` the model version and whether the
value was reconstructed; laboratory results have ``resultTime`` after
``phenomenonTime``; the maintenance log is a JSON-result Datastream per Thing; the
twin's leak candidates are a JSON-result Datastream on the Central Thing.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from flowmine.edge.core import quality

OM = "http://www.opengis.net/def/observationType/OGC-OM/2.0/"
OM_MEASUREMENT = OM + "OM_Measurement"
OM_OBSERVATION = OM + "OM_Observation"
UCUM = "http://unitsofmeasure.org/ucum.html"
QUDT = "http://qudt.org/vocab/quantitykind/"
FLOWMINE = (
    "https://github.com/SyedTahirHussan/wds-edge-cloud/blob/main/docs/sensorthings-mapping.md#"
)

OBSERVED_PROPERTIES: dict[str, dict[str, str]] = {
    "pressure": {
        "name": "pressure head",
        "definition": QUDT + "Pressure",
        "description": "Water pressure expressed as head above the node elevation",
    },
    "flow": {
        "name": "volume flow rate",
        "definition": QUDT + "VolumeFlowRate",
        "description": "Volume flow rate through a pipe or pump",
    },
    "level": {
        "name": "tank water level",
        "definition": QUDT + "Height",
        "description": "Water depth above the tank bottom",
    },
    "chlorine": {
        "name": "free chlorine",
        "definition": FLOWMINE + "free-chlorine",
        "description": "Free chlorine mass concentration",
    },
    "air_temperature": {
        "name": "air temperature",
        "definition": QUDT + "Temperature",
        "description": "Outdoor air temperature",
    },
    "maintenance": {
        "name": "maintenance activity",
        "definition": FLOWMINE + "maintenance",
        "description": "Maintenance log record (type, asset, free-text note)",
    },
    "leak_candidates": {
        "name": "leak candidate nodes",
        "definition": FLOWMINE + "leak-candidates",
        "description": "Most likely leak nodes from the twin's pressure residuals, ranked",
    },
    "event": {
        "name": "network event",
        "definition": FLOWMINE + "event",
        "description": "Event raised by the central analytics (process event or sensor fault)",
    },
}

UNITS: dict[str, dict[str, str]] = {
    "pressure": {"name": "metre of water column", "symbol": "m[H2O]", "definition": UCUM},
    "flow": {"name": "litre per second", "symbol": "L/s", "definition": UCUM},
    "level": {"name": "metre", "symbol": "m", "definition": UCUM},
    "chlorine": {"name": "milligram per litre", "symbol": "mg/L", "definition": UCUM},
    "air_temperature": {"name": "degree Celsius", "symbol": "Cel", "definition": UCUM},
    "maintenance": {"name": "none", "symbol": "", "definition": UCUM},
    "event": {"name": "none", "symbol": "", "definition": UCUM},
    "leak_candidates": {"name": "none", "symbol": "", "definition": UCUM},
}

SENSORS: dict[str, dict[str, str]] = {
    "pressure": {"name": "pressure transducer", "description": "Simulated pressure logger"},
    "flow": {"name": "flow meter", "description": "Simulated electromagnetic flow meter"},
    "level": {"name": "level transmitter", "description": "Simulated tank level transmitter"},
    "chlorine": {
        "name": "on-line chlorine analyser",
        "description": "Simulated amperometric free-chlorine analyser",
    },
    "laboratory": {
        "name": "laboratory method",
        "description": "Laboratory analysis of grab samples (simulated DPD free chlorine)",
    },
    "model": {
        "name": "EPANET 2.2 hydraulic model",
        "description": "Values computed by the flowmine twin bridge with EPANET 2.2",
    },
    "weather": {"name": "external weather service", "description": "Hourly air temperature feed"},
    "maintenance_log": {"name": "maintenance log", "description": "Records entered by operators"},
    "analytics": {
        "name": "flowmine central analytics",
        "description": "Events and corrections computed in the cloud",
    },
}


def iso(start: datetime, t_s: float) -> str:
    """ISO 8601 UTC timestamp for ``t_s`` seconds after ``start`` (second resolution)."""
    return (start + timedelta(seconds=int(t_s))).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def seconds(start: datetime, s: str) -> int:
    return int((parse_iso(s) - start).total_seconds())


def sensor_body(key: str) -> dict[str, Any]:
    d = SENSORS[key]
    return {
        "name": d["name"],
        "description": d["description"],
        "encodingType": "text/html",
        "metadata": FLOWMINE + "sensors",
        "properties": {"flowmine:key": key},
    }


def observed_property_body(key: str) -> dict[str, Any]:
    d = OBSERVED_PROPERTIES[key]
    return {
        "name": d["name"],
        "definition": d["definition"],
        "description": d["description"],
        "properties": {"flowmine:key": key},
    }


def location_body(name: str, x: float, y: float) -> dict[str, Any]:
    return {
        "name": name,
        "description": "EPANET node coordinates (schematic for Net3, not geographic)",
        "encodingType": "application/geo+json",
        "location": {"type": "Point", "coordinates": [x, y]},
    }


def sampling_point_body(node: str, x: float, y: float) -> dict[str, Any]:
    """FeatureOfInterest of a laboratory sampling point (a hydrant or tap at an EPANET
    node); the GeoJSON point uses the model's coordinates."""
    return {
        "name": f"sampling point {node}",
        "description": f"Laboratory grab-sample point at EPANET node {node}",
        "encodingType": "application/geo+json",
        "feature": {"type": "Point", "coordinates": [x, y]},
        "properties": {"epanet_node": node, "kind": "sampling_point"},
    }


def lab_observation_body(
    start: datetime,
    sampled_s: float,
    reported_s: float,
    value: float,
    datastream_id: Any,
    feature_id: Any | None = None,
    sample_id: str | None = None,
) -> dict[str, Any]:
    """A laboratory result: phenomenonTime = sampling time, resultTime = reporting time,
    linked to the sampling point's FeatureOfInterest."""
    body = observation_body(
        start, sampled_s, value, result_t_s=reported_s, datastream_id=datastream_id
    )
    if feature_id is not None:
        body["FeatureOfInterest"] = {"@iot.id": feature_id}
    if sample_id is not None:
        body["parameters"] = {"sample_id": sample_id}
    return body


def datastream_body(
    name: str,
    kind: str,
    role: str,
    thing_id: Any,
    sensor_id: Any,
    op_id: Any,
    props: dict[str, Any],
) -> dict[str, Any]:
    obs_type = (
        OM_OBSERVATION if kind in ("maintenance", "event", "leak_candidates") else OM_MEASUREMENT
    )
    return {
        "name": name,
        "description": f"{role} {OBSERVED_PROPERTIES[kind]['name']}",
        "observationType": obs_type,
        "unitOfMeasurement": UNITS[kind],
        "properties": {"role": role, **props},
        "Thing": {"@iot.id": thing_id},
        "Sensor": {"@iot.id": sensor_id},
        "ObservedProperty": {"@iot.id": op_id},
    }


def observation_body(
    start: datetime,
    t_s: float,
    value: Any,
    code: int | None = None,
    model_version: int | None = None,
    raw: float | None = None,
    result_t_s: float | None = None,
    datastream_id: Any = None,
) -> dict[str, Any]:
    """One Observation. ``code`` is the edge quality code (None for non-edge sources)."""
    body: dict[str, Any] = {
        "phenomenonTime": iso(start, t_s),
        "resultTime": iso(start, t_s if result_t_s is None else result_t_s),
        "result": value,
    }
    if code is not None:
        body["resultQuality"] = {"code": int(code), "flags": quality.names(code)}
        params: dict[str, Any] = {"reconstructed": bool(code & quality.RECONSTRUCTED)}
        if model_version is not None:
            params["model_version"] = int(model_version)
        if raw is not None and raw != value:
            params["raw"] = raw
        body["parameters"] = params
    if datastream_id is not None:
        body["Datastream"] = {"@iot.id": datastream_id}
    return body


def leak_candidates_result(
    nodes: list[str], scores: list[float], k: int = 5
) -> list[dict[str, Any]]:
    """Result of a LEAK-CANDIDATES Observation: the top-k nodes, best first."""
    return [
        {"rank": i + 1, "node": str(n), "score": round(float(s), 4)}
        for i, (n, s) in enumerate(zip(nodes[:k], scores[:k], strict=True))
    ]


def leak_candidates_body(
    start: datetime,
    t_s: float,
    nodes: list[str],
    scores: list[float],
    window_s: int,
    method: str,
    datastream_id: Any = None,
    k: int = 5,
) -> dict[str, Any]:
    """phenomenonTime is the end of the residual window; ``parameters`` give its start
    and the ranking method."""
    body = observation_body(
        start, t_s, leak_candidates_result(nodes, scores, k), datastream_id=datastream_id
    )
    body["parameters"] = {"window_start": iso(start, t_s - window_s), "method": method}
    return body


def json_size(body: dict[str, Any]) -> int:
    """Bytes of the compact JSON encoding (what an HTTP or MQTT body would carry)."""
    return len(json.dumps(body, separators=(",", ":")).encode())
