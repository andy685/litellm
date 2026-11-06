import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Final
from unittest.mock import AsyncMock, MagicMock

import pytest

from litellm.accounting.native_cost import (
    NATIVE_COST_METADATA_KEY,
    NativeCostMissing,
    NativeCostRecorded,
    capture_native_cost,
)
from litellm.models.cost_accounting import (
    CostAccountingSettings,
    ExchangeRateCache,
    ExchangeRateSnapshot,
    NativeModelPrice,
    NativePriceVersion,
)
from litellm.proxy._types import SpendLogsPayload
from litellm.proxy.spend_tracking.spend_tracking_utils import get_logging_payload
from litellm.proxy.utils import PrismaClient
from litellm.repositories.config_repository import ConfigRepository

STAMP: Final = datetime(2000, 1, 1, tzinfo=timezone.utc)


def _price() -> NativeModelPrice:
    return NativeModelPrice(
        model_id="fixture-deployment", currency="CNY", input_per_million=Decimal("2"), output_per_million=Decimal("4")
    )


def _settings(price: NativeModelPrice) -> CostAccountingSettings:
    return CostAccountingSettings(
        revision=1,
        model_prices=(price,),
        price_history=(NativePriceVersion(revision=1, price=price, effective_from=STAMP, updated_by="fixture-admin"),),
    )


def _repository(settings: CostAccountingSettings) -> tuple[ConfigRepository, MagicMock]:
    prisma: Final = MagicMock(spec=PrismaClient)
    prisma.writer_db = MagicMock()
    rate: Final = ExchangeRateCache(
        revision=1,
        snapshot=ExchangeRateSnapshot(
            usd_to_cny=Decimal("7"), rate_date=date(2000, 1, 1), fetched_at=STAMP + timedelta(days=1)
        ),
    )
    prisma.writer_db.litellm_config.find_unique = AsyncMock(
        side_effect=lambda where: SimpleNamespace(
            param_value=rate.model_dump_json()
            if where["param_name"].startswith("cost_accounting_fx:")
            else settings.model_dump_json()
        )
    )
    return ConfigRepository(prisma, use_writer=True), prisma


def _payload() -> SpendLogsPayload:
    payload: Final = get_logging_payload(
        kwargs={"response_cost": 0.1, "model": "openai/fixture-model"},
        response_obj={
            "id": "fixture-request",
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        },
        start_time=STAMP + timedelta(days=1),
        end_time=STAMP + timedelta(days=1, seconds=1),
    )
    return {
        **payload,
        "model_id": "fixture-deployment",
        "metadata": json.dumps({"usage_object": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}}),
    }


@pytest.mark.asyncio
async def test_native_cost_and_automatic_rate_are_snapshotted_without_changing_usd_spend() -> None:
    payload: Final = _payload()
    store, _prisma = _repository(_settings(_price()))
    result: Final = await capture_native_cost(payload, {}, store, False)
    recorded: Final = NativeCostRecorded.model_validate(json.loads(result["metadata"])[NATIVE_COST_METADATA_KEY])
    assert result["spend"] == payload["spend"] == 0.1
    assert recorded.snapshot.native_amount == recorded.snapshot.cny_amount == Decimal("0.0004")
    assert recorded.snapshot.usd_amount == recorded.snapshot.native_amount / Decimal("7")
    assert recorded.snapshot.rate is not None
    assert recorded.snapshot.rate.updated_by == "ECB"
    assert json.loads(payload["metadata"]) == {
        "usage_object": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
    }


@pytest.mark.asyncio
async def test_price_changes_after_a_request_do_not_reprice_that_request() -> None:
    first: Final = _price()
    later: Final = first.model_copy(update={"input_per_million": Decimal("20")})
    settings: Final = _settings(first).model_copy(
        update={
            "revision": 2,
            "model_prices": (later,),
            "price_history": (
                *_settings(first).price_history,
                NativePriceVersion(
                    revision=2, price=later, effective_from=STAMP + timedelta(days=2), updated_by="fixture-admin"
                ),
            ),
        }
    )
    store, _prisma = _repository(settings)
    result: Final = await capture_native_cost(_payload(), {}, store, False)
    recorded: Final = NativeCostRecorded.model_validate(json.loads(result["metadata"])[NATIVE_COST_METADATA_KEY])
    assert recorded.snapshot.revision == 1
    assert recorded.snapshot.native_amount == Decimal("0.0004")
    assert recorded.snapshot.price == first


@pytest.mark.asyncio
async def test_missing_output_pricing_is_recorded_as_unpriced_instead_of_free() -> None:
    price: Final = NativeModelPrice(model_id="fixture-deployment", currency="CNY", input_per_million=Decimal("2"))
    store, _prisma = _repository(_settings(price))
    result: Final = await capture_native_cost(_payload(), {}, store, False)
    missing: Final = NativeCostMissing.model_validate(json.loads(result["metadata"])[NATIVE_COST_METADATA_KEY])
    assert missing.reason == "unsupported_usage"
    assert result["spend"] == 0.1


@pytest.mark.asyncio
async def test_native_lookup_failure_preserves_the_usd_log() -> None:
    store, prisma = _repository(_settings(_price()))
    prisma.writer_db.litellm_config.find_unique.side_effect = RuntimeError("fixture unavailable")
    result: Final = await capture_native_cost(_payload(), {}, store, False)
    missing: Final = NativeCostMissing.model_validate(json.loads(result["metadata"])[NATIVE_COST_METADATA_KEY])
    assert missing.reason == "storage_unavailable"
    assert result["spend"] == 0.1


@pytest.mark.asyncio
async def test_cache_hits_are_not_priced_as_new_provider_requests() -> None:
    store, prisma = _repository(_settings(_price()))
    payload: Final[SpendLogsPayload] = {**_payload(), "cache_hit": "True"}
    result: Final = await capture_native_cost(payload, {}, store, False)
    assert json.loads(result["metadata"])[NATIVE_COST_METADATA_KEY] == {"status": "cache_hit"}
    prisma.writer_db.litellm_config.find_unique.assert_not_called()


@pytest.mark.asyncio
async def test_flat_image_prices_use_the_returned_image_count() -> None:
    price: Final = NativeModelPrice(model_id="fixture-deployment", currency="CNY", per_image=Decimal("1.25"))
    store, _prisma = _repository(_settings(price))
    result: Final = await capture_native_cost(
        _payload(), {"data": ({"url": "fixture-a"}, {"url": "fixture-b"})}, store, False
    )
    recorded: Final = NativeCostRecorded.model_validate(json.loads(result["metadata"])[NATIVE_COST_METADATA_KEY])
    assert recorded.snapshot.native_amount == Decimal("2.5")
    assert recorded.snapshot.usage.images == 2


@pytest.mark.asyncio
async def test_video_polls_without_the_settle_once_marker_are_not_charged_again() -> None:
    price: Final = NativeModelPrice(model_id="fixture-deployment", currency="CNY", per_video_second=Decimal("1"))
    store, _prisma = _repository(_settings(price))
    payload: Final[SpendLogsPayload] = {**_payload(), "call_type": "avideo_retrieve"}
    result: Final = await capture_native_cost(payload, {"seconds": "5", "status": "completed"}, store, False)
    missing: Final = NativeCostMissing.model_validate(json.loads(result["metadata"])[NATIVE_COST_METADATA_KEY])
    assert missing.reason == "unsupported_usage"
