import { act, fireEvent, render, screen } from "@testing-library/react";
import { createInstance } from "i18next";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MoneyCell } from "@/components/shared/table_cells/money_cell";
import { SpendBudgetCell } from "@/components/shared/table_cells/spend_budget_cell";
import { CurrencyProvider } from "./CurrencyProvider";
import { CurrencyNotice, CurrencySettings } from "./CurrencySettings";

const rateResponse = {
  snapshot: {
    base: "USD",
    quote: "CNY",
    usd_to_cny: "7.25",
    rate_date: "2000-01-01",
    fetched_at: "2000-01-01T12:00:00Z",
    source: "ECB",
  },
  stale: false,
};

async function showReport() {
  const i18n = createInstance();
  await i18n.init({ lng: "en", resources: { en: { translation: {} }, zh: { translation: {} } } });
  const queryClient = new QueryClient({ defaultOptions: { queries: { gcTime: 0 } } });
  const report = render(
    <QueryClientProvider client={queryClient}>
      <I18nextProvider i18n={i18n}>
        <CurrencyProvider accessToken="fixture-token">
          <CurrencyNotice />
          <CurrencySettings />
          <MoneyCell value={10} decimals={2} displayCurrency />
          <SpendBudgetCell spend={10} maxBudget={50} />
        </CurrencyProvider>
      </I18nextProvider>
    </QueryClientProvider>,
  );
  return { ...report, i18n };
}

describe("automatic currency display preferences", () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(rateResponse), { status: 200 })),
    );
  });
  afterEach(() => vi.unstubAllGlobals());

  it("loads rates automatically and retains the currency after reopening", async () => {
    const report = await showReport();
    expect(await screen.findByText("¥72.50")).toHaveAttribute("title", "10 USD");
    expect(screen.getByRole("note")).toHaveTextContent("1 USD = 7.25 CNY");
    fireEvent.change(screen.getByLabelText("Currency"), { target: { value: "USD" } });
    fireEvent.click(screen.getByRole("button", { name: "Save display preferences" }));
    expect(await screen.findByText("$10.00")).toBeInTheDocument();
    report.unmount();
    await showReport();
    expect(await screen.findByText("$10.00")).toBeInTheDocument();
    expect(screen.getByLabelText("Currency")).toHaveValue("USD");
  });

  it("keeps CNY amounts and budgets when switching languages", async () => {
    const { i18n } = await showReport();
    await screen.findByText("¥72.50");
    const meter = screen.getByRole("meter");
    const budget = meter.getAttribute("aria-valuemax");
    const spend = meter.getAttribute("aria-valuenow");
    await act(async () => {
      await i18n.changeLanguage("zh");
    });
    expect(screen.getByText("¥72.50")).toHaveAttribute("title", "10 USD");
    expect(screen.getByRole("note")).toHaveTextContent("参考折算费用");
    expect(screen.getByRole("meter")).toHaveAttribute("aria-valuemax", budget);
    expect(screen.getByRole("meter")).toHaveAttribute("aria-valuenow", spend);
    expect(screen.getByText("$10.0000")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("币种"), { target: { value: "USD" } });
    fireEvent.click(screen.getByRole("button", { name: "保存显示设置" }));
    expect(await screen.findByText("$10.00")).toBeInTheDocument();
    await act(async () => {
      await i18n.changeLanguage("en");
    });
    expect(screen.getByText("$10.00")).toBeInTheDocument();
  });

  it("shows cached rates with a warning when the provider is unavailable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ ...rateResponse, stale: true }), { status: 200 })),
    );
    await showReport();
    expect(await screen.findByText("¥72.50")).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent("last available cached rate");
  });

  it("retains USD when no valid rate is available", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("{}", { status: 503 })),
    );
    await showReport();
    expect(await screen.findByRole("alert", {}, { timeout: 5000 })).toHaveTextContent("temporarily unavailable");
    expect(screen.getByText("$10.00")).toBeInTheDocument();
  });
});
