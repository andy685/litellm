import json
from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal
from typing import Final, Literal

import httpx
from prisma.errors import PrismaError
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, ValidationError

from litellm._logging import verbose_proxy_logger
from litellm.accounting.exchange_rates import exchange_rate_http_client, get_accounting_reference_rate
from litellm.models.cost_accounting import (
    CostAccountingSettings,
    CostAccountingStore,
    InvalidAccountingSettings,
    NativeCostQuote,
    NativeModelPrice,
    NativePriceVersion,
    NativeUsage,
    UnpricedUsage,
    quote_native_cost,
)
from litellm.proxy._types import SpendLogsPayload

NATIVE_COST_METADATA_KEY: Final = "native_cost_accounting"
_METADATA: Final = TypeAdapter(Mapping[str, JsonValue])
_JSON: Final = TypeAdapter[JsonValue](JsonValue)
_TIMESTAMP: Final = TypeAdapter(datetime)
_SUPPORTED_USAGE_FIELDS: Final = frozenset(
    {
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "prompt_tokens_details",
        "completion_tokens_details",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    }
)


class NativeCostMissing(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["unpriced"] = "unpriced"
    reason: Literal["not_configured", "unsupported_usage", "invalid_settings", "storage_unavailable"]


class NativeCostRecorded(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["priced"] = "priced"
    snapshot: NativeCostQuote


class NativeCostCacheHit(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["cache_hit"] = "cache_hit"


class _PromptDetails(BaseModel):
    model_config = ConfigDict(frozen=True)

    cached_tokens: int | None = Field(default=None, ge=0)
    cache_write_tokens: int | None = Field(
        default=None, ge=0, validation_alias=AliasChoices("cache_write_tokens", "cache_creation_tokens")
    )
    audio_tokens: int | None = Field(default=None, ge=0)
    image_tokens: int | None = Field(default=None, ge=0)


class _TokenUsage(BaseModel):
    model_config = ConfigDict(frozen=True)

    prompt_tokens_details: _PromptDetails | None = None
    cache_read_input_tokens: int | None = Field(default=None, ge=0)
    cache_creation_input_tokens: int | None = Field(default=None, ge=0)


class _ImageUnits(BaseModel):
    model_config = ConfigDict(frozen=True)

    data: tuple[object, ...]


class _VideoUnits(BaseModel):
    model_config = ConfigDict(frozen=True)

    seconds: Decimal = Field(gt=0)
    status: Literal["completed"]


def _cache_count(first: int | None, second: int | None) -> int | None:
    if first is not None and second is not None and first != second:
        return None
    return first if first is not None else second or 0


def _token_units(payload: SpendLogsPayload, metadata: Mapping[str, JsonValue]) -> NativeUsage | UnpricedUsage:
    usage_object: Final = metadata.get("usage_object")
    usage: Final = _METADATA.validate_python(usage_object)
    if any(key not in _SUPPORTED_USAGE_FIELDS and bool(value) for key, value in usage.items()):
        return UnpricedUsage()
    parsed: Final = _TokenUsage.model_validate(usage)
    details: Final = parsed.prompt_tokens_details
    if details is not None and (details.audio_tokens or details.image_tokens):
        return UnpricedUsage()
    cache_read: Final = _cache_count(parsed.cache_read_input_tokens, details.cached_tokens if details else None)
    cache_write: Final = _cache_count(
        parsed.cache_creation_input_tokens, details.cache_write_tokens if details else None
    )
    if cache_read is None or cache_write is None:
        return UnpricedUsage()
    regular_input: Final = payload["prompt_tokens"] - cache_read - cache_write
    if regular_input < 0 or payload["prompt_tokens"] + payload["completion_tokens"] == 0:
        return UnpricedUsage()
    return NativeUsage(
        input_tokens=regular_input,
        output_tokens=payload["completion_tokens"],
        cached_input_tokens=cache_read,
        cache_write_tokens=cache_write,
    )


def _usage_units(
    payload: SpendLogsPayload, response: object, price: NativeModelPrice, settles_job: bool
) -> NativeUsage | UnpricedUsage:
    if "video" in payload["call_type"] and not settles_job:
        return UnpricedUsage()
    if price.per_image is not None and price.per_video_second is not None:
        return UnpricedUsage()
    token_usage: Final = (
        _token_units(payload, _METADATA.validate_json(payload["metadata"]))
        if any(unit is not None for unit in price.unit_prices[:4])
        else NativeUsage()
    )
    if isinstance(token_usage, UnpricedUsage):
        return token_usage
    if price.per_image is not None:
        images: Final = _ImageUnits.model_validate(response, from_attributes=True)
        return token_usage.model_copy(update={"images": len(images.data)}) if images.data else UnpricedUsage()
    if price.per_video_second is not None:
        video: Final = _VideoUnits.model_validate(response, from_attributes=True)
        return token_usage.model_copy(update={"video_seconds": video.seconds})
    return token_usage


def _price_at_request(
    settings: CostAccountingSettings, model_id: str | None, timestamp: datetime
) -> NativePriceVersion | None:
    candidates: Final = (
        version
        for version in settings.price_history
        if version.price.model_id == model_id and version.effective_from <= timestamp
    )
    return max(candidates, key=lambda version: (version.effective_from, version.revision), default=None)


async def _native_snapshot(
    payload: SpendLogsPayload, response: object, store: CostAccountingStore, settles_job: bool
) -> NativeCostRecorded | NativeCostMissing | NativeCostCacheHit:
    if payload["cache_hit"] == "True":
        return NativeCostCacheHit()
    try:
        settings: Final = await store.get_cost_accounting_settings()
    except (PrismaError, httpx.HTTPError, RuntimeError, TimeoutError) as error:
        verbose_proxy_logger.warning(
            "Native cost lookup failed with %s, retaining the USD spend log", type(error).__name__
        )
        return NativeCostMissing(reason="storage_unavailable")
    if isinstance(settings, InvalidAccountingSettings):
        return NativeCostMissing(reason="invalid_settings")
    parsed_timestamp: Final = _TIMESTAMP.validate_python(payload["startTime"])
    timestamp: Final = (
        parsed_timestamp.replace(tzinfo=timezone.utc) if parsed_timestamp.tzinfo is None else parsed_timestamp
    )
    version: Final = _price_at_request(settings, payload["model_id"], timestamp)
    if version is None:
        return NativeCostMissing(reason="not_configured")
    try:
        usage: Final = _usage_units(payload, response, version.price, settles_job)
    except ValidationError:
        return NativeCostMissing(reason="unsupported_usage")
    if isinstance(usage, UnpricedUsage):
        return NativeCostMissing(reason="unsupported_usage")
    reference: Final = await get_accounting_reference_rate(
        store, exchange_rate_http_client(), datetime.now(timezone.utc), timestamp.astimezone(timezone.utc).date()
    )
    automatic_settings: Final = settings.model_copy(update={"rates": (reference,) if reference is not None else ()})
    quote: Final = quote_native_cost(
        version.price, usage, timestamp.astimezone(timezone.utc).strftime("%Y-%m"), automatic_settings
    )
    if isinstance(quote, UnpricedUsage):
        return NativeCostMissing(reason="unsupported_usage")
    return NativeCostRecorded(snapshot=quote.model_copy(update={"revision": version.revision}))


async def capture_native_cost(
    payload: SpendLogsPayload, response: object, store: CostAccountingStore, settles_job: bool
) -> SpendLogsPayload:
    snapshot: Final = await _native_snapshot(payload, response, store, settles_job)
    metadata: Final = _METADATA.validate_json(payload["metadata"])
    serialized: Final = _JSON.validate_python(snapshot.model_dump(mode="json"))
    return {**payload, "metadata": json.dumps({**metadata, NATIVE_COST_METADATA_KEY: serialized})}
