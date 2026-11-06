"use client";

import { createContext, useContext, useMemo, useSyncExternalStore, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "@/contexts/AuthContext";
import { fetchAutomaticExchangeRate, type AutomaticExchangeRate } from "./exchangeRateApi";
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
  save: (currency: Currency) => void;
  exchangeRate?: AutomaticExchangeRate;
  loading: boolean;
  unavailable: boolean;
  stale: boolean;
}>;

const defaultCurrencyContext: CurrencyContextValue = {
  preferences: DEFAULT_CURRENCY_PREFERENCES,
  save: () => undefined,
  loading: false,
  unavailable: false,
  stale: false,
};

const CurrencyContext = createContext<CurrencyContextValue>(defaultCurrencyContext);

const serverSnapshot = () => null;

export function CurrencyProvider({
  children,
  store = browserStore,
  accessToken = null,
}: {
  children: ReactNode;
  store?: CurrencyStore;
  accessToken?: string | null;
}) {
  const raw = useSyncExternalStore(store.subscribe, store.read, serverSnapshot);
  const rateOptions = {
    queryKey: ["costAccountingExchangeRate"],
    queryFn: ({ signal }: { signal: AbortSignal }) => fetchAutomaticExchangeRate(accessToken, signal),
    enabled: Boolean(accessToken),
    staleTime: 15 * 60 * 1000,
    refetchInterval: 60 * 60 * 1000,
    retry: 1,
  };
  const rate = useQuery(rateOptions);
  const value = useMemo<CurrencyContextValue>(
    () => ({
      preferences: {
        ...readCurrencyPreferences(raw),
        usdToCny: rate.data?.snapshot.usd_to_cny ?? null,
        rateDate: rate.data?.snapshot.rate_date ?? null,
      },
      save: (currency) => store.write(JSON.stringify({ currency })),
      exchangeRate: rate.data,
      loading: rate.isFetching && !rate.data,
      unavailable: rate.isError && !rate.data,
      stale: rate.isError || Boolean(rate.data?.stale),
    }),
    [raw, store, rate.data, rate.isFetching, rate.isError],
  );
  return <CurrencyContext.Provider value={value}>{children}</CurrencyContext.Provider>;
}

export function AuthenticatedCurrencyProvider({ children }: { children: ReactNode }) {
  const { accessToken } = useAuth();
  return <CurrencyProvider accessToken={accessToken}>{children}</CurrencyProvider>;
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
