import { z } from "zod";

export const currencyPreferencesSchema = z.object({
  currency: z.enum(["CNY", "USD"]),
  usdToCny: z.number().positive().finite().nullable(),
  rateDate: z.iso.date().nullable(),
});

export type CurrencyPreferences = Readonly<z.infer<typeof currencyPreferencesSchema>>;
export type Currency = CurrencyPreferences["currency"];

export const DEFAULT_CURRENCY_PREFERENCES: CurrencyPreferences = {
  currency: "CNY",
  usdToCny: null,
  rateDate: null,
};

export function readCurrencyPreferences(raw: string | null): CurrencyPreferences {
  if (raw === null) return DEFAULT_CURRENCY_PREFERENCES;
  try {
    const result = currencyPreferencesSchema.safeParse(JSON.parse(raw));
    return result.success ? result.data : DEFAULT_CURRENCY_PREFERENCES;
  } catch {
    return DEFAULT_CURRENCY_PREFERENCES;
  }
}

export function convertMoney(
  amount: number,
  source: Currency,
  preferences: CurrencyPreferences,
): Readonly<{ amount: number; currency: Currency; converted: boolean }> {
  if (source === preferences.currency || preferences.usdToCny === null || preferences.rateDate === null) {
    return { amount, currency: source, converted: false };
  }
  return {
    amount: source === "USD" ? amount * preferences.usdToCny : amount / preferences.usdToCny,
    currency: preferences.currency,
    converted: true,
  };
}

export function formatMoney(
  amount: number,
  preferences: CurrencyPreferences,
  options: Readonly<{ digits?: number; source?: Currency; compact?: boolean }> = {},
): string {
  const { digits = 2, source = "USD", compact = false } = options;
  if (!Number.isFinite(amount)) return "-";
  const money = convertMoney(amount, source, preferences);
  if (!Number.isFinite(money.amount)) return "-";
  const symbol = money.currency === "CNY" ? "¥" : "$";
  const abbreviated = compact && Math.abs(money.amount) >= 1000;
  const compactDigits = Math.abs(money.amount) < 100 && money.amount !== 0 ? 2 : 0;
  const fractionalDigits = compact ? compactDigits : digits;
  const number = Intl.NumberFormat("en-US", {
    minimumFractionDigits: fractionalDigits,
    maximumFractionDigits: abbreviated ? 1 : fractionalDigits,
    notation: abbreviated ? "compact" : "standard",
  });
  const threshold = 10 ** -(compact ? 2 : digits);
  if (money.amount > 0 && money.amount < threshold) {
    return `< ${symbol}${number.format(threshold)}`;
  }
  return `${symbol}${number.format(money.amount)}`;
}
