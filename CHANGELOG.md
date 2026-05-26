# Changelog

All notable changes to Lambda ERP are recorded here.

The two packages released from this monorepo share one version and ship
together: **`lambda-erp`** (PyPI, backend) and
**`@lambda-development/erp-core`** (npm, frontend).

This project follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and [Semantic Versioning](https://semver.org/spec/v2.0.0.html). The extension
seams (registries, hooks, and the public package imports) are the
semver-governed public surface — a breaking change to a seam is a major bump.

## [Unreleased]

## [0.1.1] - 2026-05-26

First coordinated public release — the first publish to PyPI and the first
through the keyless CI pipeline, shipping both packages together. Same code as
the 0.1.0 npm bootstrap noted below.

### Added
- **Core ERP engine** (backend) — document lifecycle (quotation → sales order →
  delivery note / sales invoice → payment, plus the buying side), double-entry
  GL posting, a stock ledger with moving-average valuation, and tax calculation.
- **Multi-currency** (backend + frontend) — per-document transaction currency
  with a historical exchange-rate snapshot on each document, realized FX
  gain/loss on settlement, period-end revaluation of open foreign balances, and
  presentation-currency translation of financial statements. New sales/purchase
  documents default their currency from the party (or company) and expose a
  currency picker in the UI.
- **Stock-movement analytics** — a `stock_movements` semantic dataset over the
  stock ledger, queryable from the chat assistant (e.g. "what item moves the
  most").
- **Internationalization** — the full UI in English, German, and French with a
  language switcher; the choice is persisted per browser.
- **Open-core packaging** — the backend ships as `lambda-erp` on PyPI; the
  frontend ships as `@lambda-development/erp-core` on npm with additive override
  seams (`registerDoctype`, `registerRoute`, the nav and component registries,
  `configureBranding`, `configureApiBase`) and a `bootstrap()` entry, so a
  customer deployment depends on the packages instead of forking the repo.

## [0.1.0] - 2026-05-26

Internal npm bootstrap that created `@lambda-development/erp-core` on the
registry — required before OIDC trusted publishing can be enabled for a new npm
package. No PyPI release and no functional changes; superseded by 0.1.1.

[Unreleased]: https://github.com/lambdadevelopment/lambda-erp/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/lambdadevelopment/lambda-erp/releases/tag/v0.1.1
[0.1.0]: https://www.npmjs.com/package/@lambda-development/erp-core/v/0.1.0
