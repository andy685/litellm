from calendar import monthrange
from datetime import date, datetime, timezone
from typing import Annotated, Final, NoReturn, TypeAlias, assert_never

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from litellm.accounting.exchange_rates import (
    exchange_rate_http_client,
    get_accounting_reference_rate,
    get_exchange_rate,
)
from litellm.models.cost_accounting import (
    AccountingAccessFailure,
    AccountingConflict,
    AccountingMonth,
    CostAccountingSettings,
    CostAccountingStore,
    ExchangeRateResponse,
    ExchangeRateUnavailable,
    InvalidAccountingSettings,
    ModelPriceUpdate,
    MonthlyExchangeRate,
    MonthlyRateUpdate,
    NativeCostQuote,
    NativeUsage,
    UnpricedUsage,
    quote_native_cost,
    replace_model_price,
    replace_monthly_rate,
)
from litellm.proxy._types import LitellmUserRoles, UserAPIKeyAuth
from litellm.proxy.auth.user_api_key_auth import user_api_key_auth
from litellm.proxy.deps.repositories import CostAccountingRepositoryDep

router: Final = APIRouter(tags=["Cost Accounting"])
AccountingAuth: TypeAlias = Annotated[UserAPIKeyAuth, Depends(user_api_key_auth)]


def get_exchange_rate_client() -> httpx.AsyncClient:
    return exchange_rate_http_client()


ExchangeRateClientDep: TypeAlias = Annotated[httpx.AsyncClient, Depends(get_exchange_rate_client)]


class AccountingRatesResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    revision: int
    rates: tuple[MonthlyExchangeRate, ...]


class NativeQuoteRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    model_id: str
    month: AccountingMonth
    usage: NativeUsage


def _raise_accounting_failure(
    error: AccountingConflict
    | InvalidAccountingSettings
    | UnpricedUsage
    | AccountingAccessFailure
    | ExchangeRateUnavailable,
) -> NoReturn:
    kind: Final = error.kind
    match kind:
        case "revision_conflict":
            raise HTTPException(409, "Accounting settings changed, reload them before saving")
        case "month_locked":
            raise HTTPException(409, "The exchange rate for this month is locked")
        case "invalid_settings":
            raise HTTPException(500, "Stored accounting settings are invalid")
        case "unpriced_usage":
            raise HTTPException(422, "Some usage units have no configured native price")
        case "admin_required":
            raise HTTPException(403, "Only proxy admins can manage cost accounting")
        case "database_required":
            raise HTTPException(503, "Cost accounting requires a database")
        case "model_unpriced":
            raise HTTPException(404, "No native price is configured for this model")
        case "unavailable" | "invalid_cache":
            raise HTTPException(503, "Exchange rate is temporarily unavailable")
        case "future_date":
            raise HTTPException(422, "A future exchange rate cannot be requested")
    assert_never(kind)


def _require_accounting_admin(auth: UserAPIKeyAuth, store: CostAccountingStore | None) -> CostAccountingStore:
    if auth.user_role != LitellmUserRoles.PROXY_ADMIN:
        _raise_accounting_failure(AccountingAccessFailure("admin_required"))
    if store is None:
        _raise_accounting_failure(AccountingAccessFailure("database_required"))
    return store


async def _read_settings(store: CostAccountingStore) -> CostAccountingSettings:
    result: Final = await store.get_cost_accounting_settings()
    if isinstance(result, InvalidAccountingSettings):
        _raise_accounting_failure(result)
    return result


async def _save_settings(
    store: CostAccountingStore, candidate: CostAccountingSettings | AccountingConflict, revision: int
) -> CostAccountingSettings:
    if isinstance(candidate, AccountingConflict):
        _raise_accounting_failure(candidate)
    saved: Final = await store.save_cost_accounting_settings(candidate, revision)
    if not saved:
        _raise_accounting_failure(AccountingConflict("revision_conflict"))
    return candidate


@router.get("/cost/accounting/settings", response_model=CostAccountingSettings)
async def get_accounting_settings(auth: AccountingAuth, store: CostAccountingRepositoryDep) -> CostAccountingSettings:
    return await _read_settings(_require_accounting_admin(auth, store))


@router.get("/cost/accounting/rates", response_model=AccountingRatesResponse)
async def get_accounting_rates(auth: AccountingAuth, store: CostAccountingRepositoryDep) -> AccountingRatesResponse:
    if store is None:
        _raise_accounting_failure(AccountingAccessFailure("database_required"))
    settings: Final = await _read_settings(store)
    return AccountingRatesResponse(revision=settings.revision, rates=settings.rates)


@router.get("/cost/accounting/exchange-rate", response_model=ExchangeRateResponse)
async def get_automatic_exchange_rate(
    auth: AccountingAuth, store: CostAccountingRepositoryDep, client: ExchangeRateClientDep, as_of: date | None = None
) -> ExchangeRateResponse:
    if store is None:
        _raise_accounting_failure(AccountingAccessFailure("database_required"))
    result: Final = await get_exchange_rate(store, client, datetime.now(timezone.utc), as_of)
    if isinstance(result, ExchangeRateUnavailable):
        _raise_accounting_failure(result)
    return result


@router.patch("/cost/accounting/rates", response_model=CostAccountingSettings)
async def update_accounting_rate(
    update: MonthlyRateUpdate, auth: AccountingAuth, store: CostAccountingRepositoryDep
) -> CostAccountingSettings:
    writable: Final = _require_accounting_admin(auth, store)
    settings: Final = await _read_settings(writable)
    candidate: Final = replace_monthly_rate(settings, update, auth.user_id or "proxy-admin", datetime.now(timezone.utc))
    return await _save_settings(writable, candidate, update.revision)


@router.patch("/cost/accounting/model-prices", response_model=CostAccountingSettings)
async def update_native_model_price(
    update: ModelPriceUpdate, auth: AccountingAuth, store: CostAccountingRepositoryDep
) -> CostAccountingSettings:
    writable: Final = _require_accounting_admin(auth, store)
    settings: Final = await _read_settings(writable)
    candidate: Final = replace_model_price(settings, update, auth.user_id or "proxy-admin", datetime.now(timezone.utc))
    return await _save_settings(writable, candidate, update.revision)


@router.post("/cost/accounting/quote", response_model=NativeCostQuote)
async def quote_model_native_cost(
    quote: NativeQuoteRequest, auth: AccountingAuth, store: CostAccountingRepositoryDep, client: ExchangeRateClientDep
) -> NativeCostQuote:
    """Estimate native cost using configured prices and automatically retrieved reference rates"""
    readable: Final = _require_accounting_admin(auth, store)
    settings: Final = await _read_settings(readable)
    price: Final = next((item for item in settings.model_prices if item.model_id == quote.model_id), None)
    if price is None:
        _raise_accounting_failure(AccountingAccessFailure("model_unpriced"))
    start: Final = date.fromisoformat(f"{quote.month}-01")
    now: Final = datetime.now(timezone.utc)
    if start > now.date():
        _raise_accounting_failure(ExchangeRateUnavailable("future_date"))
    target: Final = min(date(start.year, start.month, monthrange(start.year, start.month)[1]), now.date())
    reference: Final = await get_accounting_reference_rate(readable, client, now, target)
    automatic_settings: Final = settings.model_copy(update={"rates": (reference,) if reference is not None else ()})
    result: Final = quote_native_cost(price, quote.usage, quote.month, automatic_settings)
    if isinstance(result, UnpricedUsage):
        _raise_accounting_failure(result)
    return result
