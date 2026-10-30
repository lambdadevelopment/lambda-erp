# LLM models and pricing

Verified on **2026-10-30**. The table lists the OpenAI models available to us.
Since core 1.1.8, the ERP chat and report specialist both use `gpt-6.1-sol`
with the Responses API. Chat uses reasoning effort `low`; the report specialist
uses `medium`. Since core 1.3.2, chat titles use `gpt-6-luna` (effort `low`,
override with `LAMBDA_ERP_TITLE_MODEL`); the previous `gpt-4.1-nano` was retired
by OpenAI. Transcription uses `gpt-4o-transcribe`.

## Standard text pricing

USD per **1 million tokens**, for requests with at most **272,000 input tokens**.
These rates are registered in `api/providers.py` and used for new cost estimates.

| Model | Input | Cached input | Cache write (30 min) | Cache write (12 h) | Output |
| --- | ---: | ---: | ---: | ---: | ---: |
| GPT-6 Astra | 10.00 | 1.00 | 12.50 | 25.00 | 50.00 |
| GPT-6.1 Sol | 2.00 | 0.10 | 2.50 | 5.00 | 10.00 |
| GPT-6.1 Terra | 1.00 | 0.05 | 1.25 | 2.50 | 5.00 |
| GPT-6 Luna | 0.10 | 0.01 | 0.125 | 0.25 | 0.50 |

Above 272,000 input tokens, the **whole request** uses these rates:

| Model | Input | Cached input | Cache write (30 min) | Cache write (12 h) | Output |
| --- | ---: | ---: | ---: | ---: | ---: |
| GPT-6 Astra | 20.00 | 2.00 | 25.00 | 50.00 | 75.00 |
| GPT-6.1 Sol | 4.00 | 0.20 | 5.00 | 10.00 | 15.00 |
| GPT-6.1 Terra | 2.00 | 0.10 | 2.50 | 5.00 | 7.50 |
| GPT-6 Luna | 0.20 | 0.02 | 0.25 | 0.50 | 0.75 |

Unregistered model IDs are estimated at the GPT-6.1 Sol rates.

## Accounting scope

The calculator accepts Chat Completions and Responses usage: total input,
cached input, and total output (including reasoning tokens). Cache-write rates
are recorded as reference data; the existing calculator does not account for
separately billed cache-write usage. It does not apply processing-tier or regional
premiums. These are estimates, not a replacement for the provider invoice.
See [OpenAI pricing](https://developers.openai.com/api/docs/pricing) for other tiers.

Core 1.3.2 removed models that are no longer available to us (GPT-6 Sol,
GPT-5.6 Terra, GPT-5.x, GPT-4.x). Price changes apply to new calculations only;
previously stored costs are not rewritten.

GPT-6.1 Sol is selected for both the main chat and report generation.
Keep the Responses API for GPT-6.1 Sol: it does not support tool calling
through Chat Completions or reasoning effort `none`.

## Model selection

Core 1.1.7 introduced Luna for the main chat. A production CRM interaction
showed an unsupported attribution of a person from a previous lead to another
company. Core **1.1.8** switches the chat to Sol 6.1 and adds explicit fact-grounding
rules; chat effort remains `low`, matching the earlier Terra default. The report
specialist increases from `low` to `medium`. This is a
workload-specific reliability decision, not a general comparison of model quality.
See the [release notes](../CHANGELOG.md#118---2026-10-01).

At equal token counts, Sol 6.1 costs more than Luna (20× uncached input/output,
10× cached input), but less than the previous Terra default for cached input
and output. Actual run costs also depend on reasoning/output tokens and tool turns.
