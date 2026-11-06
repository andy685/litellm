"use client";

import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Dialog, DialogContent, DialogTitle, DialogDescription, DialogTrigger } from "@/components/ui/dialog";
import { currencyPreferencesSchema, type CurrencyPreferences } from "./currency";
import { useDisplayCurrency } from "./CurrencyProvider";
import { currencyMessages } from "./messages";

function useCopy() {
  const { i18n } = useTranslation();
  return i18n.language.startsWith("zh") ? currencyMessages.zh : currencyMessages.en;
}

function CurrencyForm({
  preferences,
  onSave,
}: {
  preferences: CurrencyPreferences;
  onSave: (value: CurrencyPreferences) => void;
}) {
  const copy = useCopy();
  const [error, setError] = useState(false);
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const rate = String(data.get("rate") ?? "");
    const date = String(data.get("date") ?? "");
    const result = currencyPreferencesSchema.safeParse({
      currency: data.get("currency"),
      usdToCny: rate.trim() ? Number(rate) : null,
      rateDate: date || null,
    });
    const missingRate = result.success && (result.data.usdToCny === null || result.data.rateDate === null);
    const needsRate = result.success && result.data.currency === "CNY" && missingRate;
    if (!result.success || needsRate) {
      setError(true);
      return;
    }
    onSave(result.data);
  };
  return (
    <form onSubmit={submit} className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="display-currency">{copy.currency}</Label>
        <select
          id="display-currency"
          name="currency"
          defaultValue={preferences.currency}
          className="h-10 w-full rounded-md border bg-background px-3"
        >
          <option value="CNY">CNY · 人民币</option>
          <option value="USD">USD · US Dollar</option>
        </select>
      </div>
      <div className="space-y-2">
        <Label htmlFor="display-currency-rate">{copy.rate}</Label>
        <Input
          id="display-currency-rate"
          name="rate"
          type="number"
          step="any"
          min="0"
          defaultValue={preferences.usdToCny ?? ""}
          aria-describedby="display-currency-note"
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor="display-currency-date">{copy.date}</Label>
        <Input id="display-currency-date" name="date" type="date" defaultValue={preferences.rateDate ?? ""} />
      </div>
      <p id="display-currency-note" className="text-xs leading-5 text-muted-foreground">
        {copy.note}
      </p>
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {copy.invalid}
        </p>
      )}
      <Button type="submit" className="w-full">
        {copy.save}
      </Button>
    </form>
  );
}

export function CurrencySettings() {
  const copy = useCopy();
  const { preferences, save } = useDisplayCurrency();
  const [open, setOpen] = useState(false);
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button variant="ghost" size="sm" aria-label={copy.open} />}>
        {preferences.currency}
      </DialogTrigger>
      <DialogContent>
        <DialogTitle>{copy.title}</DialogTitle>
        <DialogDescription>{copy.description}</DialogDescription>
        <CurrencyForm
          preferences={preferences}
          onSave={(value) => {
            save(value);
            setOpen(false);
          }}
        />
      </DialogContent>
    </Dialog>
  );
}

export function CurrencyNotice() {
  const copy = useCopy();
  const { preferences } = useDisplayCurrency();
  const ready = preferences.usdToCny !== null && preferences.rateDate !== null;
  const converted = ready
    ? `${copy.estimate} · 1 USD = ${preferences.usdToCny} CNY · ${preferences.rateDate}`
    : copy.missing;
  return (
    <div
      role="note"
      className="flex flex-wrap items-center justify-between gap-2 rounded-lg border bg-card px-4 py-3 text-xs text-muted-foreground"
    >
      <p>{preferences.currency === "CNY" ? converted : copy.original}</p>
      <CurrencySettings />
    </div>
  );
}
