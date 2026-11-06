import { z } from "zod";
import { createApiClient } from "@/lib/http/client";
import { getGlobalLitellmHeaderName, getProxyBaseUrl } from "@/components/networking";

const exchangeRateSnapshotFields = {
  base: z.literal("USD"),
  quote: z.literal("CNY"),
  usd_to_cny: z.coerce.number().positive().finite(),
  rate_date: z.iso.date(),
  fetched_at: z.iso.datetime({ offset: true }),
  source: z.literal("ECB"),
};

const exchangeRateResponseSchema = z.object({
  snapshot: z.object(exchangeRateSnapshotFields),
  stale: z.boolean(),
});

export type AutomaticExchangeRate = Readonly<z.infer<typeof exchangeRateResponseSchema>>;

const api = createApiClient({ getBaseUrl: getProxyBaseUrl, getAuthHeaderName: getGlobalLitellmHeaderName });

export async function fetchAutomaticExchangeRate(
  accessToken: string | null,
  signal: AbortSignal,
): Promise<AutomaticExchangeRate> {
  const response = await api.get<unknown>("/cost/accounting/exchange-rate", { accessToken, signal });
  return exchangeRateResponseSchema.parse(response);
}
