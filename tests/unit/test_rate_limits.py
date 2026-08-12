from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

import pytest

from quantum_trader.adapters.alpaca_paper import (
    AlpacaPaperCredentials,
    AlpacaPaperHttpTransport,
    AlpacaPaperResponseError,
)
from quantum_trader.domain.rate_limits import (
    RequestBudgetExceeded,
    SlidingWindowRequestBudget,
)

NOW = datetime(2026, 8, 12, 14, 30, tzinfo=UTC)


def test_sliding_window_budget_expires_old_requests_and_reports_capacity() -> None:
    budget = SlidingWindowRequestBudget(max_requests=2, window=timedelta(seconds=60))
    assert budget.acquire(NOW) == 1
    assert budget.acquire(NOW + timedelta(seconds=1)) == 0
    assert budget.observed_count(NOW + timedelta(seconds=1)) == 2
    with pytest.raises(RequestBudgetExceeded, match="exhausted"):
        budget.acquire(NOW + timedelta(seconds=2))

    assert budget.acquire(NOW + timedelta(seconds=60)) == 0
    assert budget.observed_count(NOW + timedelta(seconds=60)) == 2


def test_request_budget_rejects_clock_regression_and_unsafe_configuration() -> None:
    budget = SlidingWindowRequestBudget(max_requests=1)
    budget.acquire(NOW)
    with pytest.raises(RequestBudgetExceeded, match="backwards"):
        budget.acquire(NOW - timedelta(seconds=1))
    with pytest.raises(ValueError, match="timezone-aware"):
        SlidingWindowRequestBudget().acquire(datetime(2026, 8, 12, 14, 30))
    with pytest.raises(ValueError, match="max_requests"):
        SlidingWindowRequestBudget(max_requests=181)
    with pytest.raises(ValueError, match="no longer"):
        SlidingWindowRequestBudget(window=timedelta(seconds=61))


def test_paper_transport_denies_budget_excess_before_opening_second_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connections: list[str] = []

    class FakeResponse:
        status = 200

        @staticmethod
        def read() -> bytes:
            return b"{}"

    class FakeConnection:
        def __init__(self, host: str, *, timeout: float) -> None:
            del timeout
            connections.append(host)

        @staticmethod
        def request(
            method: str,
            target: str,
            *,
            body: bytes | None,
            headers: Mapping[str, str],
        ) -> None:
            del method, target, body, headers

        @staticmethod
        def getresponse() -> FakeResponse:
            return FakeResponse()

        @staticmethod
        def close() -> None:
            return None

    monkeypatch.setattr(
        "quantum_trader.adapters.alpaca_paper.http.client.HTTPSConnection",
        FakeConnection,
    )
    transport = AlpacaPaperHttpTransport(
        credentials=AlpacaPaperCredentials(
            key_id="fixture-key",
            secret_key="-".join(("fixture", "secret")),
        ),
        request_budget=SlidingWindowRequestBudget(max_requests=1),
        now=lambda: NOW,
    )
    transport.request(method="GET", path="/v2/account")
    with pytest.raises(AlpacaPaperResponseError, match="budget denied"):
        transport.request(method="GET", path="/v2/account")
    assert connections == ["paper-api.alpaca.markets"]
