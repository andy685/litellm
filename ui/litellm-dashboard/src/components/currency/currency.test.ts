import { describe, expect, it } from "vitest";
import {
  convertMoney,
  DEFAULT_CURRENCY_PREFERENCES,
  formatMoney,
  readCurrencyPreferences,
  type CurrencyPreferences,
} from "./currency";

const preferences: CurrencyPreferences = { currency: "CNY", usdToCny: 7.25, rateDate: "2000-01-01" };

describe("display currency", () => {
  it("defaults to CNY without inventing an exchange rate", () => {
    expect(readCurrencyPreferences(null)).toEqual(DEFAULT_CURRENCY_PREFERENCES);
    expect(convertMoney(10, "USD", DEFAULT_CURRENCY_PREFERENCES)).toEqual({
      amount: 10,
      currency: "USD",
      converted: false,
    });
  });

  it.each([
    "broken",
    "{}",
    '{"currency":"EUR"}',
    JSON.stringify({ ...preferences, usdToCny: -1 }),
    JSON.stringify({ ...preferences, rateDate: "2000-02-31" }),
  ])("discards invalid saved preferences %s", (raw) => {
    expect(readCurrencyPreferences(raw)).toEqual(DEFAULT_CURRENCY_PREFERENCES);
  });

  it("converts USD using the saved rate without changing the original amount", () => {
    const original = Object.freeze({ amount: 12.345678, currency: "USD" as const });
    expect(convertMoney(original.amount, original.currency, preferences).amount).toBeCloseTo(
      original.amount * preferences.usdToCny!,
    );
    expect(formatMoney(original.amount, preferences)).toBe("¥89.51");
    expect(original.amount).toBe(12.345678);
  });

  it("preserves native CNY costs and converts them only for a USD display", () => {
    expect(convertMoney(100, "CNY", preferences)).toEqual({ amount: 100, currency: "CNY", converted: false });
    const result = convertMoney(100, "CNY", { ...preferences, currency: "USD" });
    expect(result.amount * preferences.usdToCny!).toBeCloseTo(100);
    expect(result.currency).toBe("USD");
  });

  it("does not convert without a date documenting the rate", () => {
    expect(formatMoney(10, { ...preferences, rateDate: null })).toBe("$10.00");
  });

  it("keeps sub-cent costs visible and handles zero and invalid amounts", () => {
    expect(formatMoney(0.000001, preferences)).toBe("< ¥0.01");
    expect(formatMoney(0.000001, preferences, { compact: true })).toBe("< ¥0.01");
    expect(formatMoney(0, preferences)).toBe("¥0.00");
    expect(formatMoney(Number.NaN, preferences)).toBe("-");
    expect(formatMoney(Number.POSITIVE_INFINITY, preferences)).toBe("-");
  });

  it("converts before rounding or abbreviating", () => {
    const costs = [0.0007, 0.0007, 0.0007] as const;
    const total = costs.reduce((sum, cost) => sum + cost, 0);
    expect(formatMoney(total, preferences, { digits: 4 })).toBe("¥0.0152");
    expect(formatMoney(2000, preferences, { compact: true })).toBe("¥14.5K");
  });
});
