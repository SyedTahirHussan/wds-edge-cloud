"""Gateway: decodes binary edge frames into SensorThings Observations.

This is the role a LoRaWAN network/application server plays: it knows each station's
sensor table (from the Thing properties) and the model version deployed there (from
the registry), so the 3-bit version on the wire can be expanded to the full number.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from flowmine.edge.core import codec, quality
from flowmine.sta import mapping
from flowmine.testbed.sensors import Sensor, StationSpec

log = logging.getLogger(__name__)


def expand_version(low3: int, deployed: int) -> int:
    """Largest version <= ``deployed`` whose low 3 bits equal ``low3``."""
    v = deployed - ((deployed - low3) % 8)
    return max(v, 0)


class Gateway:
    def __init__(
        self,
        stations: list[StationSpec],
        sensors: list[Sensor],
        ds_ids: dict[str, int],
        start: datetime,
        deployed: Callable[[str], int] | None = None,
    ) -> None:
        self.by_number = {s.number: s for s in stations}
        self.sensor_at = {(s.station, s.local_id): s for s in sensors}
        self.ds_ids = ds_ids
        self.start = start
        self.deployed = deployed or (lambda station: 1)
        self.frames = 0
        self.bytes = 0

    def decode(self, frame: bytes) -> list[dict[str, Any]]:
        """Observation bodies (each with its Datastream reference) carried by one frame."""
        number = frame[0]
        spec = self.by_number[number]
        scales = {
            s.local_id: s.resolution
            for (st, _), s in self.sensor_at.items()
            if st == spec.station_id
        }
        _, low3, recs = codec.decode(frame, scales)
        mv = expand_version(low3, self.deployed(spec.station_id))
        self.frames += 1
        self.bytes += len(frame)
        out = []
        for r in recs:
            sensor = self.sensor_at[(spec.station_id, r[0])]
            ds = self.ds_ids[sensor.sensor_id]
            if len(r) == 7:
                code = quality.make(r[5], mv)
                body = mapping.observation_body(self.start, r[1], r[2], code, mv, datastream_id=ds)
                body["phenomenonTime"] = (
                    mapping.iso(self.start, r[1]) + "/" + mapping.iso(self.start, r[1] + 900)
                )
                body["parameters"].update({"min": r[3], "max": r[4], "summary": True})
            else:
                code = quality.make(r[3], mv)
                body = mapping.observation_body(self.start, r[1], r[2], code, mv, datastream_id=ds)
            out.append(body)
        return out
