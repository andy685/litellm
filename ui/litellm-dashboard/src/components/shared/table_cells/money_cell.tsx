"use client";

import { useDisplayCurrency } from "@/components/currency/CurrencyProvider";
import { formatNumberWithCommas, getSpendString } from "@/utils/dataUtils";

interface MoneyCellProps {
  value: number | null | undefined;
  decimals?: number;
  emptyText?: string;
  showZero?: boolean;
  displayCurrency?: boolean;
}

const placeholderClassName = "block w-full whitespace-nowrap text-right tabular-nums text-muted-foreground";
const moneyClassName = "block w-full whitespace-nowrap text-right tabular-nums";

export function MoneyCell({
  value,
  decimals = 4,
  emptyText = "-",
  showZero = false,
  displayCurrency = false,
}: MoneyCellProps) {
  const { format } = useDisplayCurrency();
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return <span className={placeholderClassName}>{emptyText}</span>;
  }
  if (value === 0 && !showZero) {
    return <span className={placeholderClassName}>-</span>;
  }

  const originalValue =
    value === 0 ? `$${formatNumberWithCommas(0, decimals, false, true)}` : getSpendString(value, decimals);
  const formattedValue = displayCurrency ? format(value, decimals) : originalValue;

  return (
    <span data-slot="money-cell" className={moneyClassName} title={`${value} USD`}>
      {formattedValue}
    </span>
  );
}
