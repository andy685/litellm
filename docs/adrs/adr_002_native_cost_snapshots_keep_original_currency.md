# ADR 002 Native cost snapshots keep the original currency

Status: Accepted

## Context

Spend logs and budget counters use USD. A CNY price converted to USD and then converted back at another rate does not reproduce the original CNY cost. Repricing historical requests with the current model configuration also changes past reports

## Decision

Native model cost estimates are optional metadata on the existing spend log. They contain the original currency, usage, price and price revision. Price revisions are retained, and the request start time selects the price. The native estimate never replaces the USD spend field or budget counters

The automatic reference-rate path retrieves ECB observations, caches the latest result for one hour and retains historical dates without replacement. Display settings do not accept user-entered rates

Monthly exchange rates live in revisioned database configuration. Saving uses an atomic revision check, and locked months reject further edits. Conversion is applied to the original currency amount. CNY costs stay in CNY when there is no exchange rate

The spend writer uses the existing normalized usage metadata. It does not reshape provider responses. Missing prices, unsupported usage and failed configuration reads produce an explicit unpriced record rather than an invented zero cost. Video poll estimates require the existing settle-once marker

## Alternatives Considered

Changing the meaning of the existing spend field would also change budgets and downstream integrations. Frontend-only exchange rates cannot provide shared month locks or preserve price revisions

## Consequences

Original amounts and pricing decisions remain available for later reports. Older logs have no native snapshot and cannot be treated as verified native costs. The estimates still depend on complete and correct account pricing, and do not replace provider invoices

Enabling native accounting adds a configuration read to spend logging. It is disabled by default, and a failed native lookup preserves the existing USD log
