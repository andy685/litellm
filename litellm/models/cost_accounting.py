from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Annotated, Final, Literal, Protocol, TypeAlias

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator


def _calendar_month(value: str) -> str:
    date.fromisoformat(f"{value}-01")
    return value


AccountingCurrency: TypeAlias = Literal["CNY", "USD"]
AccountingMonth: TypeAlias = Annotated[
    str, StringConstraints(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$"), AfterValidator(_calendar_month)
]
UnitPrice: TypeAlias = Annotated[Decimal, Field(ge=0, max_digits=24, decimal_places=12)]
ExchangeRateValue: TypeAlias = Annotated[Decimal, Field(gt=0, max_digits=20, decimal_places=10)]


class MonthlyExchangeRate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    month: AccountingMonth
    usd_to_cny: ExchangeRateValue
    rate_date: date
    locked: bool = False
    updated_by: str
    updated_at: AwareDatetime


class NativeModelPrice(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    model_id: str = Field(min_length=1, max_length=256)
    currency: AccountingCurrency
    input_per_million: UnitPrice | None = None
    output_per_million: UnitPrice | None = None
    cached_input_per_million: UnitPrice | None = None
    cache_write_per_million: UnitPrice | None = None
    per_image: UnitPrice | None = None
    per_video_second: UnitPrice | None = None

    @model_validator(mode="after")
    def require_price(self) -> "NativeModelPrice":
        if all(price is None for price in self.unit_prices):
            raise ValueError("Configure at least one unit price")
        return self

    @property
    def unit_prices(self) -> tuple[Decimal | None, ...]:
        return (
            self.input_per_million,
            self.output_per_million,
            self.cached_input_per_million,
            self.cache_write_per_million,
            self.per_image,
            self.per_video_second,
        )


class NativePriceVersion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    revision: int = Field(ge=1)
    price: NativeModelPrice
    effective_from: AwareDatetime
    updated_by: str


class CostAccountingSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    revision: int = Field(default=0, ge=0)
    rates: tuple[MonthlyExchangeRate, ...] = ()
    model_prices: tuple[NativeModelPrice, ...] = ()
    price_history: tuple[NativePriceVersion, ...] = ()

    @model_validator(mode="after")
    def require_unique_entries(self) -> "CostAccountingSettings":
        if len(frozenset(rate.month for rate in self.rates)) != len(self.rates):
            raise ValueError("Each month must have one exchange rate")
        if len(frozenset(price.model_id for price in self.model_prices)) != len(self.model_prices):
            raise ValueError("Each model must have one native price")
        return self


class MonthlyRateUpdate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    revision: int = Field(ge=0)
    month: AccountingMonth
    usd_to_cny: ExchangeRateValue
    rate_date: date
    locked: bool = False


class ModelPriceUpdate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    revision: int = Field(ge=0)
    price: NativeModelPrice


@dataclass(frozen=True, slots=True)
class AccountingConflict:
    kind: Literal["revision_conflict", "month_locked"]


@dataclass(frozen=True, slots=True)
class InvalidAccountingSettings:
    kind: Literal["invalid_settings"] = "invalid_settings"


@dataclass(frozen=True, slots=True)
class AccountingAccessFailure:
    kind: Literal["admin_required", "database_required", "model_unpriced"]


class ExchangeRateSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    base: Literal["USD"] = "USD"
    quote: Literal["CNY"] = "CNY"
    usd_to_cny: ExchangeRateValue
    rate_date: date
    fetched_at: AwareDatetime
    source: Literal["ECB"] = "ECB"


class ExchangeRateCache(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    revision: int = Field(ge=1)
    snapshot: ExchangeRateSnapshot


class ExchangeRateResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot: ExchangeRateSnapshot
    stale: bool = False


@dataclass(frozen=True, slots=True)
class ExchangeRateUnavailable:
    kind: Literal["unavailable", "invalid_cache", "future_date"] = "unavailable"


class CostAccountingStore(Protocol):
    async def get_cost_accounting_settings(self) -> CostAccountingSettings | InvalidAccountingSettings: ...

    async def save_cost_accounting_settings(self, settings: CostAccountingSettings, revision: int) -> bool: ...

    async def get_exchange_rate(self, key: str) -> ExchangeRateCache | None | InvalidAccountingSettings: ...

    async def save_exchange_rate(self, key: str, snapshot: ExchangeRateSnapshot, revision: int) -> bool: ...


def replace_monthly_rate(
    settings: CostAccountingSettings,
    update: MonthlyRateUpdate,
    updated_by: str,
    updated_at: AwareDatetime,
) -> CostAccountingSettings | AccountingConflict:
    if settings.revision != update.revision:
        return AccountingConflict("revision_conflict")
    previous: Final = next((rate for rate in settings.rates if rate.month == update.month), None)
    if previous is not None and previous.locked:
        return AccountingConflict("month_locked")
    rate: Final = MonthlyExchangeRate(
        month=update.month,
        usd_to_cny=update.usd_to_cny,
        rate_date=update.rate_date,
        locked=update.locked,
        updated_by=updated_by,
        updated_at=updated_at,
    )
    retained: Final = tuple(item for item in settings.rates if item.month != update.month)
    rates: Final = tuple(sorted((*retained, rate), key=lambda item: item.month))
    return settings.model_copy(update={"revision": settings.revision + 1, "rates": rates})


def replace_model_price(
    settings: CostAccountingSettings, update: ModelPriceUpdate, updated_by: str, updated_at: AwareDatetime
) -> CostAccountingSettings | AccountingConflict:
    if settings.revision != update.revision:
        return AccountingConflict("revision_conflict")
    retained: Final = tuple(price for price in settings.model_prices if price.model_id != update.price.model_id)
    prices: Final = tuple(sorted((*retained, update.price), key=lambda price: price.model_id))
    version: Final = NativePriceVersion(
        revision=settings.revision + 1, price=update.price, effective_from=updated_at, updated_by=updated_by
    )
    return settings.model_copy(
        update={
            "revision": settings.revision + 1,
            "model_prices": prices,
            "price_history": (*settings.price_history, version),
        }
    )


class NativeUsage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    input_tokens: int = Field(default=0, ge=0, description="Uncached input tokens, excluding cache reads and writes")
    output_tokens: int = Field(default=0, ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)
    images: int = Field(default=0, ge=0)
    video_seconds: Annotated[Decimal, Field(ge=0, max_digits=20, decimal_places=8)] = Decimal(0)

    @property
    def quantities(self) -> tuple[Decimal, ...]:
        million: Final = Decimal(1_000_000)
        return (
            Decimal(self.input_tokens) / million,
            Decimal(self.output_tokens) / million,
            Decimal(self.cached_input_tokens) / million,
            Decimal(self.cache_write_tokens) / million,
            Decimal(self.images),
            self.video_seconds,
        )


class NativeCostQuote(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    month: AccountingMonth
    revision: int
    price: NativeModelPrice
    usage: NativeUsage
    native_amount: Decimal
    cny_amount: Decimal | None
    usd_amount: Decimal | None
    rate: MonthlyExchangeRate | None


@dataclass(frozen=True, slots=True)
class UnpricedUsage:
    kind: Literal["unpriced_usage"] = "unpriced_usage"


def quote_native_cost(
    price: NativeModelPrice,
    usage: NativeUsage,
    month: AccountingMonth,
    settings: CostAccountingSettings,
) -> NativeCostQuote | UnpricedUsage:
    units: Final = tuple(zip(usage.quantities, price.unit_prices, strict=True))
    if any(quantity > 0 and unit_price is None for quantity, unit_price in units):
        return UnpricedUsage()
    amount: Final = sum((quantity * unit_price for quantity, unit_price in units if unit_price is not None), Decimal(0))
    rate: Final = next((item for item in settings.rates if item.month == month), None)
    converted: Final = (
        None if rate is None else amount / rate.usd_to_cny if price.currency == "CNY" else amount * rate.usd_to_cny
    )
    return NativeCostQuote(
        month=month,
        revision=settings.revision,
        price=price,
        usage=usage,
        native_amount=amount,
        cny_amount=amount if price.currency == "CNY" else converted,
        usd_amount=amount if price.currency == "USD" else converted,
        rate=rate,
    )
