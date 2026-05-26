# Packaging & distribution plan

Status: **Phases A & B done** (2026-05-26). A: backend builds a clean
wheel/sdist via hatchling shipping `lambda_erp` + `api` (incl. the PDF
template), verified by a clean-venv install + import. B: frontend override seam
+ dual app/library build shipping `@lambda-development/erp-core`, verified by a scratch
consumer app type-checking the packed tarball. **Phases C (publish CI) & D
(customer repo template) not started** — until then, consume both packages via
the git-dependency interim path (no registry/account needed).

## Handoff — current state (2026-05-26)

A fresh session (likely on another machine / another LLM) can pick up here.
**Phases A and B are implemented and verified. Phase C (publish CI) is next.**

> ⚠️ **Commit & push before switching machines.** The Phase A/B changes may be
> uncommitted; the other machine only sees what's in git. Local auto-memory does
> NOT travel between machines — **this doc is the source of truth** for the
> handoff. Note: pushing frontend/backend code to `master` triggers the Azure
> deploy (`.github/workflows/deploy.yml`); doc-only pushes are ignored.

**Package names — decided (use these verbatim):**
- **PyPI / backend:** `lambda-erp` (confirmed free on PyPI). Build backend is
  hatchling; `python -m build` ships `lambda_erp` + `api` + the PDF template.
- **npm / frontend:** `@lambda-development/erp-core` — scope is the npm org
  `lambda-development` (the names `lambda` and `lambda-erp` were unavailable).
  This string is already used across `frontend/package.json`, `src/index.ts`,
  the README, and these docs. Don't reintroduce the old `@lambda/erp-core`.

**Registration status:**
- PyPI: account created; an org request is **pending but not a blocker** —
  publish under the personal account now, transfer to the org later.
- npm: create the **Free** org `lambda-development` to own the
  `@lambda-development` scope (Free org = unlimited public packages).
- Neither account must exist to *build/commit* the Phase C workflow; both must
  be live + trusted-publisher config saved before the first real release run.

**Re-verify A & B reproduce (no registry needed):**
```bash
# Backend wheel (from repo root)
source .venv/bin/activate && python -m build      # -> dist/lambda_erp-0.1.0-*.whl
# optional: install the wheel in a fresh venv OUTSIDE the repo, then import
#   lambda_erp, api.services, api.main, api.pdf  (proves api + template shipped)

# Frontend lib (from frontend/)
cd frontend
npm ci && npm run build         # demo app still builds — same path Docker uses
npm run build:lib               # -> dist/index.js + dist/index.d.ts (peers external)
npm pack                        # -> lambda-development-erp-core-0.1.0.tgz
# optional: install that tarball + peers in a scratch app and `tsc --noEmit`
# importing { bootstrap, AppShell, registerDoctype, registerRoute,
# registerNavGroup, registerComponent, configureBranding, configureApiBase }
```

**Key files touched in A/B (so the next session knows where things live):**
- A: `pyproject.toml` (hatchling, `[tool.hatch.build.targets.wheel] packages =
  ["lambda_erp", "api"]`).
- B seams: `frontend/src/lib/doctypes.ts` (`registerDoctype`),
  `frontend/src/routes.tsx` (`registerRoute`/`buildRoutes`),
  `frontend/src/lib/nav.tsx`, `frontend/src/lib/component-registry.tsx`,
  `frontend/src/lib/branding.ts`, `frontend/src/api/client.ts`
  (`configureApiBase` + `VITE_API_BASE`).
- B packaging: `frontend/src/index.ts` (library entry), `frontend/src/bootstrap.tsx`,
  `frontend/vite.lib.config.ts`, `frontend/tailwind.preset.ts`,
  `frontend/package.json` (`name`, `exports`, `peerDependencies`, `build:lib`,
  `private: true`).

## Goal

Distribute the core as an **open-core** product so customer deployments live in
**separate private repos that depend on published packages**, not forks:

- **Backend → public PyPI package** (`lambda_erp/` + `api/`).
- **Frontend → public npm package** (the app shell + components + registries).
- **Customer repo (private)** depends on both, registers overrides/hooks (see
  [`core-extension-architecture.md`](core-extension-architecture.md)), adds
  branding/config/secrets, and deploys. It publishes nothing.

Public packages on PyPI and npm are **free** to publish and consume. The only
private artifacts are the customer repos (private GitHub repos / deploy targets).

## One repo or two? — keep the monorepo

**Keep the current single repo and publish two packages from it.** This is
standard: monorepos routinely ship a Python package + a JS/TS package via two
scoped build/publish jobs.

- **Keep monorepo (recommended):** atomic front+back changes in one PR, single
  source of truth, the demo app keeps working, versions stay in lockstep, less
  CI/infra for a small team.
- **Split into two repos (not now):** cleaner independent release cadence, but
  you lose cross-stack atomicity (every front+back change becomes two
  coordinated PRs), double the CI, and the demo app must pull both. Only worth
  it if release cadences genuinely diverge or the team grows.

So: one repo, two packages, two scoped publish workflows.

---

## Phase A — Backend pip package

The repo is already `pip install -e .`-able, so this is mostly publish-readiness.

- [x] Ensure `pyproject.toml` ships **both** `lambda_erp` and `api` (a customer
  needs `api.services.register_doctype`, `lambda_erp.hooks`, `api.main.load_plugins`),
  and **excludes** `frontend/`, `tests/`, `docs/` from the distribution.
  **Done:** flit packages only one module matching the project name, so it
  silently dropped `api`; switched the build backend to **hatchling** with
  `[tool.hatch.build.targets.wheel] packages = ["lambda_erp", "api"]`. The PDF
  template (`api/templates/document.html`) ships as package data (hatchling
  includes non-`.py` files under the package dirs). Wheel verified to contain
  only `api/` + `lambda_erp/` (no frontend/tests/docs).
- [x] Set package `name` (kept `lambda-erp` — confirmed free on PyPI), `version`
  (`0.1.0`), description, `license = "MIT"`, `readme`, author, `requires-python
  >=3.10`, and runtime deps (unchanged list: FastAPI, pydantic, bcrypt, jose,
  weasyprint, jinja2, …).
- [x] `python -m build` → wheel + sdist; installed into a **clean venv outside
  the repo** (so cwd can't mask a missing `api`) and imported `lambda_erp`,
  `api.services`, `api.main`, `api.pdf` — surface intact, template resolves.
- [x] Public-contract import surface (`register_doctype`, `register_converter`,
  `register_hook`, `load_plugins`, the document classes) is documented in the
  README "Building a customer deployment on top of the core" section.

**Remaining for an actual publish (Phase C):** PyPI account + the name claimed,
trusted-publishing/OIDC (or `PYPI_API_TOKEN`) wired into release CI. Until then,
consume via the git-dependency interim path — no registration needed.

## Phase B — Frontend npm library (the heavy lift) — **done (2026-05-26)**

The override seam and the library packaging were built together. Decisions:
package name **`@lambda-development/erp-core`** (scoped), styling via the **"consumer scans
source"** model (the customer runs Tailwind, scans the lib's `dist`, and gets
the design tokens from a shipped preset + base stylesheet).

- [x] Override seam (the prerequisite), all additive and in-place so the demo
  app is unchanged:
  - **Doctype registry** — `registerDoctype` on the existing `lib/doctypes.ts`
    `CONFIGS` (pages read it live via `getDoctypeConfig`).
  - **Route registry** — `lib/../routes.tsx` now exposes `registerRoute` +
    `buildRoutes()` (merge-by-path, so a registered route overrides a core one).
  - **Nav registry** — `NAV_GROUPS` extracted to `lib/nav.tsx` with
    `getNavGroups`/`registerNavGroup`/`registerNavItem`.
  - **Component registry** — `lib/component-registry.tsx`
    (`registerComponent`/`getComponent`); the Dashboard index route resolves
    through it as the worked example.
  - **Branding/theme** — `lib/branding.ts` `configureBranding({ appName, logoUrl,
    tokens })` sets `:root` CSS vars + document title at runtime.
  - **Configurable API base** — `api/client.ts` reads `VITE_API_BASE` (default
    `/api`) + a runtime `configureApiBase()`.
- [x] Restructured into a dual app+library build: `src/index.ts` is the library
  entry (exports the shell, registries, branding, api client, auth, i18n,
  `bootstrap`); `src/bootstrap.tsx` holds the providers+router mount;
  `main.tsx`/`index.html` remain the demo-app entry.
- [x] `package.json`: name `@lambda-development/erp-core`, `exports` (`.`, `./styles.css` →
  `src/index.css`, `./tailwind-preset` → `tailwind.preset.ts`), `types`,
  `peerDependencies` (react/react-dom/react-router-dom/react-query/react-table/
  i18next/react-i18next/recharts/lucide as peers), `build:lib` script. The
  Tailwind theme was extracted to a shareable `tailwind.preset.ts`.
- [x] Lib build via `vite.lib.config.ts` (lib mode, externalized peers,
  `vite-plugin-dts` → ESM + `.d.ts`). Verified: demo `npm run build` still
  passes; `npm pack` → a scratch consumer app installs the tarball + peers and
  `tsc --noEmit` passes while exercising every seam.
- [x] Overrides confirmed two ways: (a) runtime registration (the registries
  above), (b) build-time `resolve.alias` whole-module swap.

## Phase C — Versioning + publish CI — **NEXT (not started)**

**Open decisions to confirm with the user before writing the workflow:**
- [ ] **Auth method** (applies to both registries): **trusted publishing / OIDC**
  (no stored tokens — recommended; supported by PyPI and by npm as of 2025) vs.
  classic API tokens in GitHub Secrets (`PYPI_API_TOKEN`, `NPM_TOKEN`). Leaning
  OIDC to match the repo's existing keyless Azure auth.
- [ ] **Flip `frontend/package.json` `"private": true`** → remove it. It's on now
  to prevent accidental `npm publish`; the release must drop it (or the workflow
  must pass a flag) to publish.

**Steps:**
- [ ] **Unified version**: both packages share the repo version, bumped together,
  one tag `vX.Y.Z`. Keep `pyproject.toml` `version` and `frontend/package.json`
  `version` in lockstep (both `0.1.0` today).
- [ ] New `.github/workflows/release.yml`, triggered on tag `v*` (separate from
  `deploy.yml`, which stays on `master` push):
  - **Python job:** `python -m build` → publish to PyPI. Trusted publishing via
    `pypa/gh-action-pypi-publish` with `permissions: id-token: write` (no token).
  - **Frontend job:** `cd frontend && npm ci && npm run build:lib && npm publish
    --access public` (a scoped package needs `--access public` to be public).
    OIDC trusted publishing, else `NODE_AUTH_TOKEN` from an `NPM_TOKEN` secret.
- [ ] Treat the public surface (extension seams) as **semver** — breaking changes
  to a seam = major bump.

**Trusted-publisher settings to save once accounts/org are live (do this in the
registry UIs, not in code):**
- PyPI → project `lambda-erp` → *Publishing* → add a GitHub publisher: owner/repo,
  workflow filename `release.yml`, optional environment. Use a **pending
  publisher** if configuring before the first manual upload.
- npm → org `lambda-development` → package `@lambda-development/erp-core` → enable
  trusted publishing for the GitHub repo + `release.yml` workflow.

## Phase D — Customer repo template (private)

- [ ] Provide a `cookiecutter`/template private repo:
  ```
  acme-erp/
    pyproject.toml          # lambda-erp-core==X.Y.Z
    acme/plugin.py          # register() -> register_doctype / register_hook
    acme/sales_invoice.py   # overrides
    frontend/package.json   # @lambda-development/erp-core ^X.Y.Z + overrides + vite alias
    config/                 # branding, features, base currency, OAuth
    deploy/                 # Dockerfile (build core+overrides), Azure config
  ```
- [ ] Deploy shape: the **customer's** frontend build (core npm package +
  overrides) produces the bundle the backend serves — still one container, built
  from core packages + customer overrides. Set `LAMBDA_ERP_PLUGINS=acme`.

---

## Interim path (before publishing)

The **identical architecture** works with **git dependencies** before any
registry publish — validate the whole open-core flow now, add registries later:

- pip: `lambda-erp-core @ git+https://github.com/<org>/lambda-erp@vX.Y.Z`
- npm: a git/submodule dependency on the same repo/tag.

This still requires Phase B's library structure for clean frontend overrides;
it only skips the npm/PyPI publish step.

## Risks / notes
- **Frontend library-mode restructure is the only heavy lift**; the backend is
  near-publishable and the rest is CI/plumbing.
- Once packages are published, the **extension seams are a public contract**
  (semver discipline).
- Publishing needs CI secrets (PyPI trusted publishing or token; npm token).
- The public demo deploy keeps using the app build; nothing about publishing
  changes it.

## Sequencing
1. ~~Frontend override seam (prerequisite for Phase B)~~ — **done.**
2. ~~Phase A (backend pip)~~ — **done.**
3. ~~Phase B (frontend npm library)~~ — **done.**
4. **Phase C (publish CI) ← next**, then Phase D (first customer repo).
   Until C is run, consume both packages via **git dependencies** (interim path).
