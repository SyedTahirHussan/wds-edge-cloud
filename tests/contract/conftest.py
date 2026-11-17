"""Contract-test fixtures: every test runs against the fake server and, when it answers,
against a real FROST-Server (marker ``frost``; URL from ``FROST_URL``)."""

from __future__ import annotations

import os
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from flowmine.sta.client import StaClient
from flowmine.sta.fake import create_app

FROST_URL = os.environ.get("FROST_URL", "http://localhost:8080/FROST-Server/v1.1")


def frost_reachable() -> bool:
    try:
        return httpx.get(FROST_URL, timeout=3.0).status_code == 200
    except httpx.HTTPError:
        return False


@pytest.fixture(params=["fake", pytest.param("frost", marks=pytest.mark.frost)])
def sta(request: pytest.FixtureRequest) -> Iterator[StaClient]:
    if request.param == "fake":
        http = TestClient(create_app(base_url="http://testserver"))
        yield StaClient("http://testserver/v1.1", http)
    else:
        if not frost_reachable():
            pytest.skip(f"FROST-Server not reachable at {FROST_URL}")
        yield StaClient(FROST_URL)
