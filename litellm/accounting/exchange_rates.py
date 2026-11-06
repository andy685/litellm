from datetime import date, datetime, timedelta, timezone
from typing import Final, Literal

import httpx
from prisma.errors import PrismaError
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from litellm._logging import verbose_proxy_logger
from litellm.llms.custom_httpx.http_handler import (
    get_async_httpx_client,  # pyright: ignore[reportUnknownVariableType]  # HTTP factory has untyped optional params
)
from litellm.models.cost_accounting import (
    CostAccountingStore,
    ExchangeRateResponse,
    ExchangeRateSnapshot,
    ExchangeRateUnavailable,
    ExchangeRateValue,
    InvalidAccountingSettings,
    MonthlyExchangeRate,
)

EXCHANGE_RATE_URL: Final = "https://api.frankfurter.dev/v2/providers/ecb/rates"
EXCHANGE_RATE_REFRESH: Final = timedelta(hours=1)


def exchange_rate_http_client() -> httpx.AsyncClient:
    return get_async_httpx_client("cost_accounting_exchange_rates").client


class _ProviderRate(BaseModel):
    model_config = ConfigDict(frozen=True)

    date: date
    base: Literal["USD"]
    quote: Literal["CNY"]
    rate: ExchangeRateValue


_PROVIDER_RATES: Final = TypeAdapter(tuple[_ProviderRate, ...])


async def _fetch_rate(
    client: httpx.AsyncClient, requested: date | None, now: datetime
) -> ExchangeRateSnapshot | ExchangeRateUnavailable:
    params: Final = {
        "base": "usd",
        "quotes": "cny",
        **({"from": (requested - timedelta(days=14)).isoformat(), "to": requested.isoformat()} if requested else {}),
    }
    try:
        response: Final = await client.get(EXCHANGE_RATE_URL, params=params, timeout=10, follow_redirects=False)
        response.raise_for_status()
        rates: Final = _PROVIDER_RATES.validate_json(response.content)
    except (httpx.HTTPError, ValidationError):
        return ExchangeRateUnavailable()
    last_date: Final = requested or now.astimezone(timezone.utc).date()
    eligible: Final = tuple(rate for rate in rates if rate.date <= last_date)
    if not eligible:
        return ExchangeRateUnavailable()
    rate: Final = max(eligible, key=lambda item: item.date)
    return ExchangeRateSnapshot(usd_to_cny=rate.rate, rate_date=rate.date, fetched_at=now)


async def get_exchange_rate(
    store: CostAccountingStore, client: httpx.AsyncClient, now: datetime, requested: date | None = None
) -> ExchangeRateResponse | ExchangeRateUnavailable:
    today: Final = now.astimezone(timezone.utc).date()
    if requested is not None and requested > today:
        return ExchangeRateUnavailable("future_date")
    historical: Final = requested is not None and requested < today
    key: Final = requested.isoformat() if historical and requested is not None else "latest"
    cached: Final = await store.get_exchange_rate(key)
    if isinstance(cached, InvalidAccountingSettings):
        return ExchangeRateUnavailable("invalid_cache")
    if cached is not None and (historical or timedelta(0) <= now - cached.snapshot.fetched_at < EXCHANGE_RATE_REFRESH):
        return ExchangeRateResponse(snapshot=cached.snapshot)
    fetched: Final = await _fetch_rate(client, requested if historical else None, now)
    if isinstance(fetched, ExchangeRateUnavailable):
        fallback: Final = cached if cached is not None else await store.get_exchange_rate(key)
        if isinstance(fallback, InvalidAccountingSettings):
            return ExchangeRateUnavailable("invalid_cache")
        return ExchangeRateResponse(snapshot=fallback.snapshot, stale=True) if fallback is not None else fetched
    if cached is not None and fetched.rate_date < cached.snapshot.rate_date:
        return ExchangeRateResponse(snapshot=cached.snapshot, stale=True)
    revision: Final = cached.revision if cached is not None else 0
    saved: Final = await store.save_exchange_rate(key, fetched, revision)
    if saved:
        return ExchangeRateResponse(snapshot=fetched)
    winner: Final = await store.get_exchange_rate(key)
    if winner is None or isinstance(winner, InvalidAccountingSettings):
        return ExchangeRateUnavailable("invalid_cache")
    return ExchangeRateResponse(snapshot=winner.snapshot)


async def get_accounting_reference_rate(
    store: CostAccountingStore, client: httpx.AsyncClient, now: datetime, requested: date
) -> MonthlyExchangeRate | None:
    try:
        result: Final = await get_exchange_rate(store, client, now, requested)
    except (PrismaError, httpx.HTTPError, RuntimeError, TimeoutError) as error:
        verbose_proxy_logger.warning("Automatic exchange rate lookup failed with %s", type(error).__name__)
        return None
    if isinstance(result, ExchangeRateUnavailable):
        return None
    return MonthlyExchangeRate(
        month=requested.strftime("%Y-%m"),
        usd_to_cny=result.snapshot.usd_to_cny,
        rate_date=result.snapshot.rate_date,
        locked=True,
        updated_by=result.snapshot.source,
        updated_at=result.snapshot.fetched_at,
    )
