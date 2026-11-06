"use client";

import { createContext, useContext, useMemo, useSyncExternalStore, type ReactNode } from "react";
import {
  getLocalStorageItem,
  setLocalStorageItem,
  emitLocalStorageChange,
  LOCAL_STORAGE_EVENT,
} from "@/utils/localStorageUtils";
import {
  DEFAULT_CURRENCY_PREFERENCES,
  formatMoney,
  readCurrencyPreferences,
  type Currency,
  type CurrencyPreferences,
} from "./currency";

const STORAGE_KEY = "litellm_display_currency";

export interface CurrencyStore {
  readonly read: () => string | null;
  readonly write: (value: string) => void;
  readonly subscribe: (listener: () => void) => () => void;
}

const browserStore: CurrencyStore = {
  read: () => getLocalStorageItem(STORAGE_KEY),
  write: (value) => {
    setLocalStorageItem(STORAGE_KEY, value);
    emitLocalStorageChange(STORAGE_KEY);
  },
  subscribe: (listener) => {
    const onStorage = (event: StorageEvent) => {
      if (event.key === STORAGE_KEY || event.key === null) listener();
    };
    const onLocalChange = (event: Event) => {
      if (event instanceof CustomEvent && event.detail?.key === STORAGE_KEY) listener();
    };
    window.addEventListener("storage", onStorage);
    window.addEventListener(LOCAL_STORAGE_EVENT, onLocalChange);
    return () => {
      window.removeEventListener("storage", onStorage);
      window.removeEventListener(LOCAL_STORAGE_EVENT, onLocalChange);
    };
  },
};

type CurrencyContextValue = Readonly<{
  preferences: CurrencyPreferences;
  save: (preferences: CurrencyPreferences) => void;
}>;

const CurrencyContext = createContext<CurrencyContextValue>({
  preferences: DEFAULT_CURRENCY_PREFERENCES,
  save: () => undefined,
});

const serverSnapshot = () => null;

export function CurrencyProvider({ children, store = browserStore }: { children: ReactNode; store?: CurrencyStore }) {
  const raw = useSyncExternalStore(store.subscribe, store.read, serverSnapshot);
  const value = useMemo<CurrencyContextValue>(
    () => ({
      preferences: readCurrencyPreferences(raw),
      save: (preferences) => store.write(JSON.stringify(preferences)),
    }),
    [raw, store],
  );
  return <CurrencyContext.Provider value={value}>{children}</CurrencyContext.Provider>;
}

export function useDisplayCurrency() {
  const context = useContext(CurrencyContext);
  return {
    ...context,
    currency:
      context.preferences.usdToCny !== null && context.preferences.rateDate !== null
        ? context.preferences.currency
        : "USD",
    format: (amount: number, digits = 2, source: Currency = "USD") =>
      formatMoney(amount, context.preferences, { digits, source }),
    formatCompact: (amount: number) => formatMoney(amount, context.preferences, { compact: true }),
  };
}
