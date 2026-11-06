import json
from collections.abc import Iterator
from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Final
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from litellm.models.cost_accounting import CostAccountingSettings, MonthlyExchangeRate
from litellm.proxy._types import LitellmUserRoles, UserAPIKeyAuth
from litellm.proxy.auth.user_api_key_auth import user_api_key_auth
from litellm.proxy.deps.database import get_database
from litellm.proxy.ui_crud_endpoints.cost_accounting_endpoints import router
from litellm.proxy.utils import PrismaClient
from litellm.repositories.database import Database
from tests._support.native_database import native_database

STAMP: Final = datetime(2000, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def accounting_client() -> Iterator[tuple[TestClient, MagicMock]]:
    prisma: Final = MagicMock(spec=PrismaClient)
    prisma.db = MagicMock()
    prisma.writer_db = MagicMock()
    prisma.db.litellm_config.find_unique = AsyncMock(return_value=None)
    prisma.writer_db.litellm_config.find_unique = AsyncMock(return_value=None)
    prisma.writer_db.query_raw = AsyncMock(return_value=({"param_name": "cost_accounting"},))
    database: Final = Database(prisma=prisma, native=native_database())
    app: Final = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_database] = lambda: database
    app.dependency_overrides[user_api_key_auth] = lambda: UserAPIKeyAuth(
        user_id="fixture-admin", user_role=LitellmUserRoles.PROXY_ADMIN
    )
    with TestClient(app) as client:
        yield client, prisma


def _rate_body(revision: int = 0) -> dict[str, str | int | bool]:
    return {
        "revision": revision,
        "month": "2000-01",
        "usd_to_cny": "7.25",
        "rate_date": "2000-01-01",
        "locked": True,
    }


def test_monthly_rate_save_persists_the_server_revision_and_author(
    accounting_client: tuple[TestClient, MagicMock],
) -> None:
    client, prisma = accounting_client
    response: Final = client.patch("/cost/accounting/rates", json=_rate_body())
    assert response.status_code == 200
    settings: Final = CostAccountingSettings.model_validate(response.json())
    assert settings.revision == 1
    assert len(settings.rates) == 1
    assert settings.rates[0].updated_by == "fixture-admin"
    assert settings.rates[0].locked is True
    _sql, key, payload, revision = TypeAdapter(tuple[str, str, str, int]).validate_python(
        prisma.writer_db.query_raw.call_args.args
    )
    assert CostAccountingSettings.model_validate_json(payload) == settings
    assert key == "cost_accounting"
    assert revision == 0


def test_a_concurrent_database_change_rejects_the_write(accounting_client: tuple[TestClient, MagicMock]) -> None:
    client, prisma = accounting_client
    prisma.writer_db.query_raw.return_value = ()
    response: Final = client.patch("/cost/accounting/rates", json=_rate_body())
    assert response.status_code == 409
    assert response.json() == {"detail": "Accounting settings changed, reload them before saving"}


def test_locked_month_changes_never_reach_the_database_write(accounting_client: tuple[TestClient, MagicMock]) -> None:
    client, prisma = accounting_client
    settings: Final = CostAccountingSettings(
        revision=1,
        rates=(
            MonthlyExchangeRate(
                month="2000-01",
                usd_to_cny="7.25",
                rate_date=date(2000, 1, 1),
                locked=True,
                updated_by="fixture-admin",
                updated_at=STAMP,
            ),
        ),
    )
    prisma.writer_db.litellm_config.find_unique.return_value = SimpleNamespace(param_value=settings.model_dump_json())
    response: Final = client.patch("/cost/accounting/rates", json=_rate_body(revision=1))
    assert response.status_code == 409
    assert response.json() == {"detail": "The exchange rate for this month is locked"}
    prisma.writer_db.query_raw.assert_not_called()


def test_non_admin_save_is_forbidden_even_without_a_database(accounting_client: tuple[TestClient, MagicMock]) -> None:
    client, prisma = accounting_client
    client.app.dependency_overrides[get_database] = lambda: None
    client.app.dependency_overrides[user_api_key_auth] = lambda: UserAPIKeyAuth(
        user_id="fixture-user", user_role=LitellmUserRoles.INTERNAL_USER
    )
    response: Final = client.patch("/cost/accounting/rates", json=_rate_body())
    assert response.status_code == 403
    assert response.json() == {"detail": "Only proxy admins can manage cost accounting"}
    prisma.writer_db.query_raw.assert_not_called()


@pytest.mark.parametrize("raw", ("not-json", json.dumps({"revision": -1})))
def test_corrupt_settings_are_reported_instead_of_replaced_with_zero_costs(
    accounting_client: tuple[TestClient, MagicMock], raw: str
) -> None:
    client, prisma = accounting_client
    prisma.writer_db.litellm_config.find_unique.return_value = SimpleNamespace(param_value=raw)
    response: Final = client.get("/cost/accounting/settings")
    assert response.status_code == 500
    assert response.json() == {"detail": "Stored accounting settings are invalid"}


def test_invalid_month_and_rate_are_rejected_without_a_write(accounting_client: tuple[TestClient, MagicMock]) -> None:
    client, prisma = accounting_client
    response: Final = client.patch("/cost/accounting/rates", json={**_rate_body(), "month": "2000-13", "usd_to_cny": 0})
    assert response.status_code == 422
    prisma.writer_db.query_raw.assert_not_called()


def test_missing_native_prices_do_not_return_a_zero_quote(accounting_client: tuple[TestClient, MagicMock]) -> None:
    client, prisma = accounting_client
    response: Final = client.post(
        "/cost/accounting/quote",
        json={"model_id": "fixture-deployment", "month": "2000-01", "usage": {"input_tokens": 100}},
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "No native price is configured for this model"}
