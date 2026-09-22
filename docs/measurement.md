# Measurement and pricing

[中文](measurement.zh-CN.md) · [Home](../README.md)

## Count reported usage

SuperLight reads Codex completion events exported over OTLP JSON. Input and output come from the agent's reported counts; it does not tokenize text or infer tokens from encrypted network bytes.

- Total tokens = input + output.
- Cached input is a subset of input; reasoning output is a subset of output.
- Missing values remain `null`, distinct from an explicitly reported zero.
- Startup or background completion events can be included, so the ledger may exceed the count shown for one CLI turn.

Where a response ID is available, records deduplicate by service, session, and response ID. Otherwise, an identical timestamp and normalized usage deduplicate retransmitted events. Events retransmitted with a new timestamp cannot always be recognized.

Codex CLI 0.144.1 has been tested with ChatGPT authentication. Its exporter can leave standard OTLP timestamps at zero; SuperLight also accepts the RFC3339 `event.timestamp` attribute and retains nanosecond precision.

## Calendar periods

Today begins at local midnight; this week begins on Monday; this month begins on the first day. The collector's local timezone determines boundaries, including daylight saving changes. Future-dated events are excluded from the dashboard. These are calendar periods, not rolling 24-hour, 7-day, or 30-day windows.

Only received completion events count. Disabled exporters, lost events, interrupted requests without usage, and activity before collection started are not reconstructed. Unknown and missing-price records remain visible as incomplete estimates.

## Estimate API-equivalent cost

```text
USD = ((input - cached input) × input rate
     + cached input × cached rate
     + output × output rate) / 1,000,000
```

Rates are USD per million tokens. Each response is priced separately, including any configured long-context tier; period totals sum those estimates. **This is not a subscription bill or a remaining-quota calculation.**

The bundled [price snapshot](../superlight/prices.json) was checked on 2026-09-22 against [OpenAI's pricing page](https://developers.openai.com/api/docs/pricing). It is not automatically updated. Lookup uses exact model IDs; unknown aliases are not guessed.

The estimate excludes tool charges, Fast-mode premiums, regional uplifts, modality-specific fees and cache-write premiums. For models with cache-write pricing, the current adapter does not expose enough detail to separate that charge. Price changes revalue historical tokens when the dashboard, summary, or export is queried; the price snapshot date is displayed.

## Custom prices

Copy the bundled file, edit it, and select it in App Settings or with `--prices`. A custom file **replaces** the bundled catalog.

```json
{
  "currency": "USD",
  "as_of": "2026-09-22",
  "basis": "my_reference_prices",
  "models": {
    "my-model": {
      "input": "2.00",
      "cached_input": "0.20",
      "output": "12.00",
      "long_context": {
        "above_input_tokens": 272000,
        "input": "4.00",
        "cached_input": "0.40",
        "output": "18.00"
      }
    }
  }
}
```

The long-context tier is optional and applies only when a response's input exceeds the threshold. Decimal strings preserve exact rates. Restart the collector after editing its active price file.

## Data and privacy

SQLite is the authoritative ledger. Stored fields include timestamps, model names, token counts, and service/session/response identifiers for deduplication. Prompts, answers, credentials, user emails, and tool output are discarded rather than stored. The dashboard export contains period aggregates; CLI JSONL export contains normalized records.

References: [Codex telemetry](https://learn.chatgpt.com/docs/config-file/config-advanced#observability-and-telemetry), [OTLP protocol](https://opentelemetry.io/docs/specs/otlp/).
