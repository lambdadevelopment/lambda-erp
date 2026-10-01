# LLM models and pricing

Verified against the official OpenAI model pages on **2026-09-29**.
Since core 1.1.8, the ERP chat and report specialist both use `gpt-6.1-sol`
with the Responses API and reasoning effort `low`. Title generation and
transcription keep their existing models.

## Standard text pricing

USD per **1 million tokens**, for requests with at most **272,000 input tokens**.
These rates are registered in `api/providers.py` and used for new cost estimates.

| Model | Input | Cached input | Cache write | Output |
| --- | ---: | ---: | ---: | ---: |
| [GPT-6 Astra](https://developers.openai.com/api/docs/models/gpt-6-astra) | 10.00 | 1.00 | 12.50 | 50.00 |
| [GPT-6 Sol](https://developers.openai.com/api/docs/models/gpt-6-sol) | 2.00 | 0.20 | 2.50 | 10.00 |
| [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna) | 0.10 | 0.01 | 0.125 | 0.50 |
| [GPT-6.1 Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol) | 2.00 | 0.10 | 2.50 | 10.00 |
| [GPT-5.6 Terra](https://developers.openai.com/api/docs/models/gpt-5.6-terra) | 2.00 | 0.20 | 2.50 | 12.00 |

Above 272,000 input tokens, the **whole request** uses these rates:

| Model | Input | Cached input | Cache write | Output |
| --- | ---: | ---: | ---: | ---: |
| GPT-6 Astra | 20.00 | 2.00 | 25.00 | 75.00 |
| GPT-6 Sol | 4.00 | 0.40 | 5.00 | 15.00 |
| GPT-6 Luna | 0.20 | 0.02 | 0.25 | 0.75 |
| GPT-6.1 Sol | 4.00 | 0.20 | 5.00 | 15.00 |
| GPT-5.6 Terra | 4.00 | 0.40 | 5.00 | 18.00 |

## Accounting scope

The calculator accepts Chat Completions and Responses usage: total input,
cached input, and total output (including reasoning tokens). Cache-write rates
are recorded as reference data; the existing calculator does not account for
separately billed cache-write usage. It does not apply processing-tier or regional
premiums. These are estimates, not a replacement for the provider invoice.
See [OpenAI pricing](https://developers.openai.com/api/docs/pricing) for other tiers.

The previous Terra entry used 2.50/0.25/15.00; the corrected Standard rates apply
to new calculations only. Previously stored costs are not rewritten.

GPT-6.1 Sol has the same input price as Terra, half the cached-input price, and
16.7% cheaper output. It is selected for both the main chat and report generation.
Keep the Responses API for GPT-6.1 Sol: it does not support tool calling
through Chat Completions or reasoning effort `none`.

## Model selection

Core 1.1.7 introduced Luna for the main chat. A production CRM interaction
showed an unsupported attribution of a person from a previous lead to another
company. Core **1.1.8** switches the chat to Sol 6.1 and adds explicit fact-grounding
rules; the effort remains `low`, matching the earlier Terra default. This is a
workload-specific reliability decision, not a general comparison of model quality.
See the [release notes](../CHANGELOG.md#118---2026-10-01).

At equal token counts, Sol 6.1 costs more than Luna (20× uncached input/output,
10× cached input), but less than the previous Terra default for cached input
and output. Actual run costs also depend on reasoning/output tokens and tool turns.
