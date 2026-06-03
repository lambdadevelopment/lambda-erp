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

## [0.1.3] - 2026-06-03

### Fixed
- **`@lambda-development/erp-core` could not be production-built by a consumer.**
  The analytics report worker was referenced with a bare
  `new Worker(new URL("…", import.meta.url))`, so the library build emitted an
  **absolute** `/assets/report-runtime.worker-*.js` URL. A downstream app
  bundling the package resolved that leading-slash path against its own
  `public/` dir and failed with "Could not resolve entry module". The worker is
  now imported with `?worker&inline`, embedding it as a blob in `dist/index.js`
  — no external asset for consumers to resolve. Backend (`lambda-erp`) is
  unchanged; it ships at 0.1.3 only to keep the two packages in lockstep.

## [0.1.2] - 2026-05-26

### Fixed
- npm publish failed provenance verification on 0.1.1 because
  `frontend/package.json` declared no `repository` field. Added it (plus
  `description`, `license`, `homepage`, `bugs`). No functional changes since
  0.1.1. Net effect on versions: **npm** goes 0.1.0 → 0.1.2 (npm 0.1.1 never
  shipped); **PyPI** published 0.1.1 normally, so PyPI includes 0.1.1 and npm
  does not. Both registries are aligned again at 0.1.2.

## [0.1.1] - 2026-05-26

First public release of `lambda-erp` to PyPI via the keyless CI pipeline. (npm
0.1.1 failed provenance verification and shipped as 0.1.2 instead — see above.)
Same code as the 0.1.0 npm bootstrap noted below.

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

[Unreleased]: https://github.com/lambdadevelopment/lambda-erp/compare/v0.1.3...HEAD
[0.1.3]: https://github.com/lambdadevelopment/lambda-erp/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/lambdadevelopment/lambda-erp/releases/tag/v0.1.2
[0.1.1]: https://github.com/lambdadevelopment/lambda-erp/releases/tag/v0.1.1
[0.1.0]: https://www.npmjs.com/package/@lambda-development/erp-core/v/0.1.0
