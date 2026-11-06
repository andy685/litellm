import { act, fireEvent, render, screen } from "@testing-library/react";
import { createInstance } from "i18next";
import { I18nextProvider } from "react-i18next";
import { beforeEach, describe, expect, it } from "vitest";
import { MoneyCell } from "@/components/shared/table_cells/money_cell";
import { SpendBudgetCell } from "@/components/shared/table_cells/spend_budget_cell";
import { CurrencyProvider } from "./CurrencyProvider";
import { CurrencyNotice } from "./CurrencySettings";

async function showReport() {
  const i18n = createInstance();
  await i18n.init({ lng: "en", resources: { en: { translation: {} }, zh: { translation: {} } } });
  const report = render(
    <I18nextProvider i18n={i18n}>
      <CurrencyProvider>
        <CurrencyNotice />
        <MoneyCell value={10} decimals={2} displayCurrency />
        <SpendBudgetCell spend={10} maxBudget={50} />
      </CurrencyProvider>
    </I18nextProvider>,
  );
  return { ...report, i18n };
}

function saveRate() {
  fireEvent.click(screen.getByRole("button", { name: "Currency settings" }));
  fireEvent.change(screen.getByLabelText("CNY per USD"), { target: { value: "7.25" } });
  fireEvent.change(screen.getByLabelText("Exchange rate date"), { target: { value: "2000-01-01" } });
  fireEvent.click(screen.getByRole("button", { name: "Save display preferences" }));
}

describe("currency display preferences", () => {
  beforeEach(() => window.localStorage.clear());

  it("shows original USD until configured and retains the rate and currency after reopening", async () => {
    const report = await showReport();
    expect(screen.getByText("$10.00")).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent("Set an exchange rate");
    saveRate();
    expect(await screen.findByText("¥72.50")).toHaveAttribute("title", "10 USD");
    expect(screen.getByRole("note")).toHaveTextContent("1 USD = 7.25 CNY");
    report.unmount();
    await showReport();
    expect(await screen.findByText("¥72.50")).toBeInTheDocument();
  });

  it("keeps the currency and amount when the language changes and converts only on an explicit currency change", async () => {
    const { i18n } = await showReport();
    const budgetMeter = screen.getByRole("meter");
    const originalBudget = budgetMeter.getAttribute("aria-valuemax");
    const originalSpend = budgetMeter.getAttribute("aria-valuenow");
    saveRate();
    expect(budgetMeter).toHaveAttribute("aria-valuemax", originalBudget);
    expect(budgetMeter).toHaveAttribute("aria-valuenow", originalSpend);
    expect(screen.getByText("$10.0000")).toBeInTheDocument();
    await act(async () => {
      await i18n.changeLanguage("zh");
    });
    expect(screen.getByText("¥72.50")).toHaveAttribute("title", "10 USD");
    expect(screen.getByRole("note")).toHaveTextContent("折算费用");
    fireEvent.click(screen.getByRole("button", { name: "币种设置" }));
    fireEvent.change(screen.getByLabelText("币种"), { target: { value: "USD" } });
    fireEvent.click(screen.getByRole("button", { name: "保存显示设置" }));
    expect(await screen.findByText("$10.00")).toHaveAttribute("title", "10 USD");
    await act(async () => {
      await i18n.changeLanguage("en");
    });
    expect(screen.getByText("$10.00")).toBeInTheDocument();
  });

  it("rejects a missing exchange rate without changing the displayed amount", async () => {
    await showReport();
    fireEvent.click(screen.getByRole("button", { name: "Currency settings" }));
    fireEvent.click(screen.getByRole("button", { name: "Save display preferences" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Enter a positive exchange rate");
    expect(screen.getByText("$10.00")).toBeInTheDocument();
  });
});
