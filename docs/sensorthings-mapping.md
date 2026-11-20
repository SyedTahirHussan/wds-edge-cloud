# SensorThings mapping

All observations, laboratory results, maintenance records, simulated values and
analytics events live in an OGC SensorThings API v1.1 server [R16]. The code that
builds the bodies is `src/flowmine/sta/mapping.py`; provisioning is
`src/flowmine/sta/provision.py`. The JSON below was produced by that code against
the in-memory test server (ids differ on FROST-Server).

## Thing: one edge station

A station is a `Thing`. Its `properties` carry the EPANET element of every sensor it
hosts, the supply zone (DMA) and the station class. The twin bridge reads its
element mapping from here, so no separate mapping file exists.

```json
{
  "name": "TK-1",
  "description": "Edge station (tank) at EPANET node 1",
  "properties": {
    "station_class": "tank",
    "station_number": 2,
    "dma": "DMA-Lake",
    "epanet_node": "1",
    "tank_area_m2": 527.1784612530366,
    "inflow_sign": -1.0,
    "sensors": [
      {"sensor_id": "L-1", "kind": "level", "local_id": 0, "epanet_element": "1",
       "epanet_type": "tank", "period_s": 900, "resolution": 0.01},
      {"sensor_id": "F-40", "kind": "flow", "local_id": 1, "epanet_element": "40",
       "epanet_type": "pipe", "period_s": 300, "resolution": 0.1}
    ]
  }
}
```

`station_number` and `local_id` are the one-byte identifiers used in the binary
uplink frame. `inflow_sign` says whether positive meter flow fills the tank; in Net3
every tank pipe starts at the tank, so it is -1. Net3 has no DMAs, so `dma` is the
reservoir that is hydraulically closest along the pipes.

## Location

```json
{
  "name": "node 10",
  "description": "EPANET node coordinates (schematic for Net3, not geographic)",
  "encodingType": "application/geo+json",
  "location": {"type": "Point", "coordinates": [9.0, 27.85]}
}
```

Net3 coordinates are drawing coordinates, not longitudes and latitudes. A model
exported from a GIS (Giswater exports EPANET files, the route Aigües de Manresa uses
[R7]) would give real coordinates in the same field.

## Sensor

Measured, laboratory and simulated values must stay distinguishable, so each has its
own `Sensor` entity: one per instrument type, plus these two:

```json
{"name": "laboratory method",
 "description": "Laboratory analysis of grab samples (simulated DPD free chlorine)",
 "encodingType": "text/html",
 "metadata": "https://github.com/SyedTahirHussan/wds-edge-cloud/blob/main/docs/sensorthings-mapping.md#sensors",
 "properties": {"flowmine:key": "laboratory"}}
```

```json
{"name": "EPANET 2.2 hydraulic model",
 "description": "Values computed by the flowmine twin bridge with EPANET 2.2",
 "encodingType": "text/html",
 "metadata": "https://github.com/SyedTahirHussan/wds-edge-cloud/blob/main/docs/sensorthings-mapping.md#sensors",
 "properties": {"flowmine:key": "model"}}
```

The other `Sensor` entities are the pressure transducer, flow meter, level
transmitter, on-line chlorine analyser, external weather service, maintenance log and
the central analytics.

## ObservedProperty

Pressure, flow, level and air temperature point to QUDT quantity kinds
(`http://qudt.org/vocab/quantitykind/Pressure`, `VolumeFlowRate`, `Height`,
`Temperature`). QUDT has no quantity kind for free chlorine, so that one, and the
maintenance and event properties, point to an anchor in this document:

```json
{"name": "free chlorine",
 "definition": "https://github.com/SyedTahirHussan/wds-edge-cloud/blob/main/docs/sensorthings-mapping.md#free-chlorine",
 "description": "Free chlorine mass concentration",
 "properties": {"flowmine:key": "chlorine"}}
```

### Free chlorine

Free available chlorine (HOCl + OCl⁻) as a mass concentration of Cl₂, in mg/L.

## Datastream

One Datastream per measured sensor, with the unit in UCUM (`m[H2O]` for pressure
head, `L/s`, `m`, `mg/L`, `Cel`):

```json
{
  "name": "C-15",
  "description": "measured free chlorine",
  "observationType": "http://www.opengis.net/def/observationType/OGC-OM/2.0/OM_Measurement",
  "unitOfMeasurement": {"name": "milligram per litre", "symbol": "mg/L",
                        "definition": "http://unitsofmeasure.org/ucum.html"},
  "properties": {"role": "measured", "sensor_id": "C-15", "station": "LG-15",
                 "epanet_element": "15", "epanet_type": "junction", "local_id": 1,
                 "period_s": 900, "resolution": 0.01}
}
```

The Datastream name is the key the code uses. Other roles, on the same Thing unless
noted:

| name | role | Sensor |
|---|---|---|
| `LAB-C-15` | laboratory | laboratory method |
| `SIM-P-15` | simulated | EPANET 2.2 hydraulic model |
| `RES-P-15` | residual (measured − simulated) | EPANET 2.2 hydraulic model |
| `VIRT-123` | virtual sensor at an unmonitored node (own Thing `VN-123`) | EPANET 2.2 hydraulic model |
| `MAINT-LG-15` | maintenance records for the station's assets | maintenance log |
| `WEATHER` | hourly air temperature (Thing `External`) | external weather service |
| `EVENTS` | events raised by the central analytics (Thing `Central`) | central analytics |
| `UPLINK-STATS` | byte counters per station and policy, demo only (Thing `Central`) | central analytics |
| `LEAK-CANDIDATES` | the twin's five best-ranked leak nodes, JSON result (Thing `Central`) | EPANET 2.2 hydraulic model |

## Observation

A value the edge rejected and reconstructed. `resultQuality` carries the quality code
(flag bits 0-7, model version in bits 8-15) and the names of the set flags;
`parameters` carries the model version, whether the value is a reconstruction, and
the raw value when it differs:

```json
{
  "phenomenonTime": "2025-07-22T00:00:00Z",
  "resultTime": "2025-07-22T00:00:00Z",
  "result": 0.41,
  "resultQuality": {"code": 848, "flags": ["MODEL_RESIDUAL", "RECONSTRUCTED"]},
  "parameters": {"reconstructed": true, "model_version": 3, "raw": 0.73},
  "Datastream": {"@iot.id": 83}
}
```

The standard types `resultQuality` as `DQ_Element`. This mapping stores a small JSON
object there instead, which FROST-Server accepts. A consumer that expects ISO 19157
elements would need an adapter.

A laboratory result. The sample was taken at `phenomenonTime`, the result was
reported at `resultTime`, 31 hours later in this example. The cloud only uses a
result once its `resultTime` has passed. Since v0.3 each result links the
FeatureOfInterest of its sampling point and carries the laboratory's sample id:

```json
{
  "phenomenonTime": "2025-07-22T10:00:00Z",
  "resultTime": "2025-07-23T17:00:00Z",
  "result": 0.44,
  "Datastream": {"@iot.id": 86},
  "FeatureOfInterest": {"@iot.id": 12},
  "parameters": {"sample_id": "LAB-00123"}
}
```

## FeatureOfInterest: laboratory sampling point

Provisioning creates one FeatureOfInterest per sampling point (in this testbed, the
five nodes with a chlorine analyser), found again by name on a second run:

```json
{
  "name": "sampling point 15",
  "description": "Laboratory grab-sample point at EPANET node 15",
  "encodingType": "application/geo+json",
  "feature": {"type": "Point", "coordinates": [38.68, 23.76]},
  "properties": {"epanet_node": "15", "kind": "sampling_point"}
}
```

The analyser's own observations carry no FeatureOfInterest, so FROST-Server derives
one from the Thing's Location. A laboratory result and an analyser reading at the same
node therefore point to different FeatureOfInterest entities with the same
coordinates. Linking the analyser readings to the sampling point as well would let a
query join them by feature; I left it out because the gateway would have to look up
the feature id for every frame.

## Leak candidates

The live twin posts the five best-ranked leak nodes as one Observation on
`LEAK-CANDIDATES` (observation type `OM_Observation`). `phenomenonTime` is the end of
the residual window, `parameters` give its start and the ranking method:

```json
{
  "phenomenonTime": "2025-07-24T12:00:00Z",
  "resultTime": "2025-07-24T12:00:00Z",
  "result": [
    {"rank": 1, "node": "123", "score": 0.91},
    {"rank": 2, "node": "125", "score": 0.88},
    {"rank": 3, "node": "121", "score": 0.8},
    {"rank": 4, "node": "120", "score": 0.7},
    {"rank": 5, "node": "119", "score": 0.66}
  ],
  "Datastream": {"@iot.id": 91},
  "parameters": {"window_start": "2025-07-24T06:00:00Z", "method": "angle"}
}
```

An interval `phenomenonTime` (`start/end`) would describe the window more exactly; the
instant plus `parameters` was kept because the client's frame loader parses instants
only. The scores in this example are made up.

## Maintenance log

SensorThings has no event or work-order entity. The log is stored as observations on
a `MAINT-<station>` Datastream per Thing (observation type `OM_Observation`), with a
JSON object as the result:

```json
{
  "phenomenonTime": "2025-07-23T13:25:00Z",
  "resultTime": "2025-07-23T13:25:00Z",
  "result": {"type": "valve_operation", "asset": "169",
             "note": "isolation valve on pipe 169 closed for works, 3.2 h"},
  "Datastream": {"@iot.id": 87}
}
```

Alternatives considered:

- A category observation (`OM_CategoryObservation`) with the record type as result
  and the note in `parameters`. Filtering by type becomes `result eq
  'valve_operation'`, a plain string comparison, but the asset id and note move into
  `parameters`, where they are less visible.
- One Datastream per asset. This is closer to a work-order system, but most assets
  (pipes, valves) are not Things, so they would need artificial Things.
- Keeping the log outside SensorThings. The analytics would then read two systems.

What the chosen mapping loses:

- The duration of an operation is only in the free-text note and in the convention of
  the analytics (alarms are suppressed for a fixed window). `phenomenonTime` could
  carry an interval (`start/end`); this is not used because operators record the
  start, not the end.
- A record about a pipe or valve is attached to the nearest station's Thing, not to
  the asset itself.
- There is no link between a record and the observations it explains. The cloud
  re-derives it from asset, time and graph distance
  (`flowmine.central.maintenance`).

## Bulk transfer and MQTT

The client posts observations with FROST-Server's JSON batch request (`POST $batch`
with `{"requests": [...]}`). The v1.1 standard itself defines only multipart batches;
the JSON form is a FROST extension. MQTT uses the v1.1 topics
`v1.1/Datastreams(<id>)/Observations`, for publishing new observations and for
subscribing. The contract tests (`tests/contract/`) check both against FROST-Server
2.8.2, together with the laboratory result linked to its sampling point and the
`LEAK-CANDIDATES` JSON result.
