from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Final
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from pydantic import TypeAdapter

from litellm.accounting.exchange_rates import get_exchange_rate
from litellm.models.cost_accounting import (
    ExchangeRateCache,
    ExchangeRateResponse,
    ExchangeRateSnapshot,
    ExchangeRateUnavailable,
)
from litellm.proxy.utils import PrismaClient
from litellm.repositories.config_repository import ConfigRepository

NOW: Final = datetime(2000, 1, 10, 12, tzinfo=timezone.utc)


def _snapshot() -> ExchangeRateSnapshot:
    return ExchangeRateSnapshot(usd_to_cny=Decimal("7.25"), rate_date=date(2000, 1, 7), fetched_at=NOW)


def _repository(cached: ExchangeRateCache | None = None) -> tuple[ConfigRepository, MagicMock]:
    prisma: Final = MagicMock(spec=PrismaClient)
    prisma.writer_db = MagicMock()
    row: Final = SimpleNamespace(param_value=cached.model_dump_json()) if cached else None
    prisma.writer_db.litellm_config.find_unique = AsyncMock(return_value=row)
    prisma.writer_db.query_raw = AsyncMock(return_value=({"param_name": "cost_accounting_fx:latest"},))
    return ConfigRepository(prisma, use_writer=True), prisma


@pytest.mark.asyncio
async def test_automatic_rate_is_validated_and_saved_with_its_publication_date() -> None:
    store, prisma = _repository()
    transport: Final = httpx.MockTransport(
        lambda request: httpx.Response(200, json=[{"date": "2000-01-07", "base": "USD", "quote": "CNY", "rate": 7.25}])
    )
    async with httpx.AsyncClient(transport=transport) as client:
        result: Final = await get_exchange_rate(store, client, NOW)
    assert result == ExchangeRateResponse(snapshot=_snapshot())
    _sql, key, payload, revision = TypeAdapter(tuple[str, str, str, int]).validate_python(
        prisma.writer_db.query_raw.call_args.args
    )
    assert ExchangeRateCache.model_validate_json(payload).snapshot == _snapshot()
    assert key == "cost_accounting_fx:latest"
    assert revision == 0


@pytest.mark.asyncio
async def test_fresh_server_cache_avoids_an_external_request() -> None:
    cache: Final = ExchangeRateCache(revision=1, snapshot=_snapshot())
    store, prisma = _repository(cache)
    handler: Final = MagicMock(return_value=httpx.Response(503))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result: Final = await get_exchange_rate(store, client, NOW + timedelta(minutes=30))
    assert result == ExchangeRateResponse(snapshot=cache.snapshot)
    handler.assert_not_called()
    prisma.writer_db.query_raw.assert_not_called()


@pytest.mark.asyncio
async def test_unavailable_provider_keeps_the_last_valid_rate_and_marks_it_stale() -> None:
    cache: Final = ExchangeRateCache(revision=1, snapshot=_snapshot())
    store, prisma = _repository(cache)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(503))) as client:
        result: Final = await get_exchange_rate(store, client, NOW + timedelta(hours=2))
    assert result == ExchangeRateResponse(snapshot=cache.snapshot, stale=True)
    prisma.writer_db.query_raw.assert_not_called()


@pytest.mark.asyncio
async def test_provider_failure_without_cache_does_not_invent_an_exchange_rate() -> None:
    store, prisma = _repository()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(503))) as client:
        result: Final = await get_exchange_rate(store, client, NOW)
    assert result == ExchangeRateUnavailable()
    prisma.writer_db.query_raw.assert_not_called()


@pytest.mark.asyncio
async def test_failed_refresh_can_use_a_cache_created_by_another_worker() -> None:
    store, prisma = _repository()
    winner: Final = ExchangeRateCache(revision=1, snapshot=_snapshot())
    prisma.writer_db.litellm_config.find_unique.side_effect = (
        None,
        SimpleNamespace(param_value=winner.model_dump_json()),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(503))) as client:
        result: Final = await get_exchange_rate(store, client, NOW)
    assert result == ExchangeRateResponse(snapshot=winner.snapshot, stale=True)
    prisma.writer_db.query_raw.assert_not_called()


@pytest.mark.asyncio
async def test_historical_dates_use_the_last_published_rate_before_that_date() -> None:
    store, prisma = _repository()
    rates: Final = (
        {"date": "2000-01-06", "base": "USD", "quote": "CNY", "rate": 7.0},
        {"date": "2000-01-07", "base": "USD", "quote": "CNY", "rate": 7.25},
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=rates))
    ) as client:
        result: Final = await get_exchange_rate(store, client, NOW, date(2000, 1, 8))
    assert result == ExchangeRateResponse(snapshot=_snapshot())
    _sql, key, _payload, _revision = TypeAdapter(tuple[str, str, str, int]).validate_python(
        prisma.writer_db.query_raw.call_args.args
    )
    assert key == "cost_accounting_fx:2000-01-08"


@pytest.mark.asyncio
async def test_a_saved_historical_rate_is_not_replaced_when_live_rates_change() -> None:
    cache: Final = ExchangeRateCache(revision=1, snapshot=_snapshot())
    store, prisma = _repository(cache)
    handler: Final = MagicMock(return_value=httpx.Response(503))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result: Final = await get_exchange_rate(store, client, NOW + timedelta(days=30), date(2000, 1, 8))
    assert result == ExchangeRateResponse(snapshot=cache.snapshot)
    handler.assert_not_called()
    prisma.writer_db.query_raw.assert_not_called()


@pytest.mark.asyncio
async def test_a_concurrent_cache_write_returns_the_persisted_winner() -> None:
    store, prisma = _repository()
    winner: Final = ExchangeRateCache(revision=1, snapshot=_snapshot().model_copy(update={"usd_to_cny": Decimal("7")}))
    prisma.writer_db.litellm_config.find_unique.side_effect = (
        None,
        SimpleNamespace(param_value=winner.model_dump_json()),
    )
    prisma.writer_db.query_raw.return_value = ()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json=[{"date": "2000-01-07", "base": "USD", "quote": "CNY", "rate": 7.25}]
            )
        )
    ) as client:
        result: Final = await get_exchange_rate(store, client, NOW)
    assert result == ExchangeRateResponse(snapshot=winner.snapshot)
