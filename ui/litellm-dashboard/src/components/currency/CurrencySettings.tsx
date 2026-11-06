"use client";

import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { useDisplayCurrency } from "./CurrencyProvider";
import { currencyMessages } from "./messages";

function useCopy() {
  const { i18n } = useTranslation();
  return i18n.language.startsWith("zh") ? currencyMessages.zh : currencyMessages.en;
}

export function CurrencySettings() {
  const copy = useCopy();
  const { preferences, save, exchangeRate, loading, unavailable, stale } = useDisplayCurrency();
  const checkedAt = exchangeRate
    ? new Date(exchangeRate.snapshot.fetched_at).toLocaleString(copy === currencyMessages.zh ? "zh-CN" : "en-US", {
        hour12: false,
      })
    : "";
  const [saved, setSaved] = useState(false);
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const currency = new FormData(event.currentTarget).get("currency");
    if (currency !== "CNY" && currency !== "USD") return;
    save(currency);
    setSaved(true);
  };
  return (
    <section aria-labelledby="currency-settings-title" className="mb-6 space-y-4 rounded-xl border bg-card p-6">
      <div>
        <h2 id="currency-settings-title" className="text-lg font-semibold">
          {copy.title}
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">{copy.description}</p>
      </div>
      <form onSubmit={submit} className="flex flex-wrap items-end gap-3">
        <div className="min-w-48 space-y-2">
          <Label htmlFor="display-currency">{copy.currency}</Label>
          <select
            id="display-currency"
            name="currency"
            defaultValue={preferences.currency}
            onChange={() => setSaved(false)}
            className="h-10 w-full rounded-md border bg-background px-3"
          >
            <option value="CNY">CNY · 人民币</option>
            <option value="USD">USD · US Dollar</option>
          </select>
        </div>
        <Button type="submit">{copy.save}</Button>
        {saved && (
          <p role="status" className="py-2 text-sm text-muted-foreground">
            {copy.saved}
          </p>
        )}
      </form>
      <div className="space-y-2 rounded-lg bg-muted/40 p-4 text-sm">
        <p className="font-medium">{copy.automatic}</p>
        {exchangeRate && (
          <p className="tabular-nums">
            1 USD = {exchangeRate.snapshot.usd_to_cny} CNY · {exchangeRate.snapshot.rate_date}
          </p>
        )}
        {exchangeRate && (
          <p className="text-xs text-muted-foreground">
            {copy.source} · {copy.sourceName} (ECB) · {copy.updated} {checkedAt}
          </p>
        )}
        {loading && <p role="status">{copy.loading}</p>}
        {unavailable && <p role="alert">{copy.missing}</p>}
        {stale && exchangeRate && <p role="status">{copy.stale}</p>}
      </div>
      <p className="text-xs leading-5 text-muted-foreground">{copy.note}</p>
    </section>
  );
}

export function CurrencyNotice() {
  const copy = useCopy();
  const { preferences, exchangeRate, loading, stale } = useDisplayCurrency();
  const converted = exchangeRate
    ? `${copy.estimate} · 1 USD = ${exchangeRate.snapshot.usd_to_cny} CNY · ${exchangeRate.snapshot.rate_date}`
    : copy.missing;
  const message = loading ? copy.loading : converted;
  return (
    <div role="note" className="rounded-lg border bg-card px-4 py-3 text-xs text-muted-foreground">
      <p>{preferences.currency === "CNY" ? message : copy.original}</p>
      {stale && exchangeRate && <p className="mt-1">{copy.stale}</p>}
    </div>
  );
}
