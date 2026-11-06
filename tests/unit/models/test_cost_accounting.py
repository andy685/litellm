from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Final

import pytest
from pydantic import ValidationError

from litellm.models.cost_accounting import (
    AccountingConflict,
    CostAccountingSettings,
    ModelPriceUpdate,
    MonthlyExchangeRate,
    MonthlyRateUpdate,
    NativeCostQuote,
    NativeModelPrice,
    NativeUsage,
    UnpricedUsage,
    quote_native_cost,
    replace_model_price,
    replace_monthly_rate,
)

STAMP: Final = datetime(2000, 1, 1, tzinfo=timezone.utc)


def _rate(month: str, value: str, *, locked: bool = False) -> MonthlyExchangeRate:
    return MonthlyExchangeRate(
        month=month,
        usd_to_cny=Decimal(value),
        rate_date=date(2000, 1, 1),
        locked=locked,
        updated_by="fixture-admin",
        updated_at=STAMP,
    )


def test_cny_cost_is_computed_from_native_prices_without_an_exchange_rate() -> None:
    price: Final = NativeModelPrice(
        model_id="fixture-deployment", currency="CNY", input_per_million=Decimal("2"), output_per_million=Decimal("5")
    )
    quote: Final = quote_native_cost(
        price, NativeUsage(input_tokens=500_000, output_tokens=200_000), "2000-01", CostAccountingSettings()
    )
    assert isinstance(quote, NativeCostQuote)
    assert quote.native_amount == Decimal("2")
    assert quote.cny_amount == quote.native_amount
    assert quote.usd_amount is None
    assert quote.rate is None


def test_usd_cost_uses_the_rate_for_its_month() -> None:
    settings: Final = CostAccountingSettings(rates=(_rate("2000-01", "7"), _rate("2000-02", "8")))
    price: Final = NativeModelPrice(model_id="fixture-deployment", currency="USD", per_image=Decimal("0.23"))
    january: Final = quote_native_cost(price, NativeUsage(images=3), "2000-01", settings)
    february: Final = quote_native_cost(price, NativeUsage(images=3), "2000-02", settings)
    assert isinstance(january, NativeCostQuote)
    assert isinstance(february, NativeCostQuote)
    assert january.native_amount == february.native_amount == Decimal("0.69")
    assert january.cny_amount == january.native_amount * settings.rates[0].usd_to_cny
    assert february.cny_amount == february.native_amount * settings.rates[1].usd_to_cny


def test_video_and_cache_units_are_priced_separately_without_rounding_each_unit() -> None:
    price: Final = NativeModelPrice(
        model_id="fixture-deployment",
        currency="CNY",
        input_per_million=Decimal("2"),
        output_per_million=Decimal("4"),
        cached_input_per_million=Decimal("0.5"),
        cache_write_per_million=Decimal("3"),
        per_video_second=Decimal("0.13"),
    )
    usage: Final = NativeUsage(
        input_tokens=100, output_tokens=50, cached_input_tokens=80, cache_write_tokens=20, video_seconds=Decimal("1.25")
    )
    quote: Final = quote_native_cost(price, usage, "2000-01", CostAccountingSettings())
    assert isinstance(quote, NativeCostQuote)
    assert quote.native_amount == Decimal("0.163")
    assert NativeCostQuote.model_validate_json(quote.model_dump_json()) == quote


def test_missing_prices_do_not_turn_usage_into_a_free_request() -> None:
    price: Final = NativeModelPrice(model_id="fixture-deployment", currency="CNY", input_per_million=Decimal("2"))
    result: Final = quote_native_cost(
        price, NativeUsage(input_tokens=100, output_tokens=1), "2000-01", CostAccountingSettings()
    )
    assert result == UnpricedUsage()


def test_an_explicit_zero_price_is_distinct_from_a_missing_price() -> None:
    price: Final = NativeModelPrice(model_id="fixture-deployment", currency="CNY", input_per_million=Decimal(0))
    quote: Final = quote_native_cost(price, NativeUsage(input_tokens=100), "2000-01", CostAccountingSettings())
    assert isinstance(quote, NativeCostQuote)
    assert quote.cny_amount == Decimal(0)


@pytest.mark.parametrize(("value", "locked"), (("8", True), ("7", False)))
def test_a_locked_month_cannot_be_repriced_or_unlocked(value: str, locked: bool) -> None:
    settings: Final = CostAccountingSettings(revision=2, rates=(_rate("2000-01", "7", locked=True),))
    update: Final = MonthlyRateUpdate(
        revision=2, month="2000-01", usd_to_cny=Decimal(value), rate_date=date(2000, 1, 1), locked=locked
    )
    assert replace_monthly_rate(settings, update, "other-admin", STAMP) == AccountingConflict("month_locked")


def test_updating_one_month_preserves_other_months_and_advances_the_revision() -> None:
    january: Final = _rate("2000-01", "7", locked=True)
    settings: Final = CostAccountingSettings(revision=1, rates=(january,))
    update: Final = MonthlyRateUpdate(revision=1, month="2000-02", usd_to_cny=Decimal("8"), rate_date=date(2000, 2, 1))
    changed: Final = replace_monthly_rate(settings, update, "other-admin", STAMP)
    assert isinstance(changed, CostAccountingSettings)
    assert changed.revision == settings.revision + 1
    assert changed.rates == (
        january,
        _rate("2000-02", "8").model_copy(update={"rate_date": date(2000, 2, 1), "updated_by": "other-admin"}),
    )
    assert settings.rates == (january,)


def test_stale_price_updates_do_not_overwrite_newer_settings() -> None:
    price: Final = NativeModelPrice(model_id="fixture-deployment", currency="CNY", per_image=Decimal("1"))
    settings: Final = CostAccountingSettings(revision=2, model_prices=(price,))
    result: Final = replace_model_price(settings, ModelPriceUpdate(revision=1, price=price), "fixture-admin", STAMP)
    assert result == AccountingConflict("revision_conflict")


@pytest.mark.parametrize("value", ("0", "-1", "NaN", "Infinity"))
def test_invalid_rates_are_rejected(value: str) -> None:
    with pytest.raises(ValidationError):
        MonthlyRateUpdate(revision=0, month="2000-01", usd_to_cny=Decimal(value), rate_date=date(2000, 1, 1))


def test_quotes_keep_their_original_price_snapshot_after_a_price_change() -> None:
    price: Final = NativeModelPrice(model_id="fixture-deployment", currency="CNY", per_image=Decimal("1.25"))
    settings: Final = CostAccountingSettings(model_prices=(price,))
    quote: Final = quote_native_cost(price, NativeUsage(images=2), "2000-01", settings)
    changed: Final = replace_model_price(
        settings,
        ModelPriceUpdate(revision=0, price=price.model_copy(update={"per_image": Decimal("2.5")})),
        "fixture-admin",
        STAMP,
    )
    assert isinstance(changed, CostAccountingSettings)
    updated_quote: Final = quote_native_cost(changed.model_prices[0], NativeUsage(images=2), "2000-01", changed)
    assert isinstance(quote, NativeCostQuote)
    assert isinstance(updated_quote, NativeCostQuote)
    assert quote.native_amount == Decimal("2.5")
    assert updated_quote.native_amount == Decimal("5")
