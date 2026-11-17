"""Cloud model registry: versioned forecaster packages with hash, window, metrics, status.

Each registration stores the package bytes under ``<root>/<sensor>/v<version>.json``
and an entry in ``index.json``. A version is ``candidate`` until promoted to
``active``; promoting retires the previous active version, and ``rollback`` brings it
back. Stations report the version they run inside every observation (low 3 bits on
the wire, expanded by the gateway against ``deployed``).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from flowmine.edge.node import EdgeNode


@dataclass
class Entry:
    sensor_id: str
    version: int
    kind: str
    sha256: str
    train_start_s: int
    train_end_s: int
    metrics: dict[str, float]
    status: str  # candidate | active | retired


class ModelRegistry:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = root / "index.json"
        self.entries: list[Entry] = []
        if self.index_path.exists():
            self.entries = [Entry(**e) for e in json.loads(self.index_path.read_text())]

    def _save(self) -> None:
        self.index_path.write_text(
            json.dumps([asdict(e) for e in self.entries], indent=1, sort_keys=True)
        )

    def versions(self, sensor_id: str) -> list[Entry]:
        return sorted(
            (e for e in self.entries if e.sensor_id == sensor_id), key=lambda e: e.version
        )

    def register(
        self,
        sensor_id: str,
        kind: str,
        blob: bytes,
        window: tuple[int, int],
        metrics: dict[str, float],
    ) -> Entry:
        version = max((e.version for e in self.versions(sensor_id)), default=0) + 1
        path = self.root / sensor_id / f"v{version}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blob)
        e = Entry(
            sensor_id,
            version,
            kind,
            hashlib.sha256(blob).hexdigest(),
            window[0],
            window[1],
            metrics,
            "candidate",
        )
        self.entries.append(e)
        self._save()
        return e

    def blob(self, e: Entry) -> bytes:
        data = (self.root / e.sensor_id / f"v{e.version}.json").read_bytes()
        if hashlib.sha256(data).hexdigest() != e.sha256:
            raise ValueError(f"stored package for {e.sensor_id} v{e.version} is corrupted")
        return data

    def active(self, sensor_id: str) -> Entry | None:
        return next((e for e in self.versions(sensor_id) if e.status == "active"), None)

    def promote(self, sensor_id: str, version: int) -> None:
        if all(e.version != version for e in self.versions(sensor_id)):
            raise KeyError(f"{sensor_id} has no version {version}")
        for e in self.versions(sensor_id):
            if e.status == "active":
                e.status = "retired"
            if e.version == version:
                e.status = "active"
        self._save()

    def rollback(self, sensor_id: str) -> Entry | None:
        """Re-activate the newest retired version; the current active one is retired."""
        vs = self.versions(sensor_id)
        cur = self.active(sensor_id)
        prev = [e for e in vs if e.status == "retired" and (cur is None or e.version < cur.version)]
        if not prev:
            return None
        if cur is not None:
            cur.status = "retired"
        prev[-1].status = "active"
        self._save()
        return prev[-1]

    def deploy(self, node: EdgeNode, sensor_id: str) -> bool:
        """Send the active version to a station; the station verifies the hash."""
        e = self.active(sensor_id)
        if e is None:
            return False
        return node.install_model(self.blob(e), e.sha256)

    def deployed(self, station_sensors: list[str]) -> int:
        """Highest active version among a station's sensors (what the gateway expands to)."""
        vs = [e.version for s in station_sensors if (e := self.active(s)) is not None]
        return max(vs, default=1)
