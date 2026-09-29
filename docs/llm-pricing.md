# LLM models and pricing

Verified against the official OpenAI model pages on **2026-09-29**.
The ERP chat defaults to `gpt-6-luna` using the Responses API and reasoning
effort `low`. The report specialist remains `gpt-5.6-terra` (`low`); title
generation and transcription keep their existing models.

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
16.7% cheaper output. Registering its prices does not select it for chat.
If selected later, keep the Responses API: GPT-6.1 Sol does not support tool
calling through Chat Completions or reasoning effort `none`.

## Develop trial

Deploy this core commit to `lambda-erp-internal` Develop via an immutable source
archive pin. No release or production promotion is needed for the trial.
