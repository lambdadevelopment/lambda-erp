# Releasing

Two packages ship together from this monorepo under **one version** and **one
tag `vX.Y.Z`**:

| Package | Registry | Source |
|---------|----------|--------|
| `lambda-erp` | PyPI | `lambda_erp/` + `api/` |
| `@lambda-development/erp-core` | npm | `frontend/` |

Publishing is automated by [`.github/workflows/release.yml`](../.github/workflows/release.yml),
triggered by pushing a `v*` tag, using **OIDC trusted publishing** — no stored
tokens. The changelog of *what* shipped is [`CHANGELOG.md`](../CHANGELOG.md);
this doc is *how* to ship.

---

## One-time setup

All done as of the `0.1.2` keyless release (2026-05-27) — the OIDC publish on
both registries proves the trusted publishers and the `release` environment are
in place. Kept here as the setup record for a fresh repo.

### PyPI — `lambda-erp`
- [x] PyPI account with 2FA.
- [x] **Pending publisher** added (Your projects → Publishing): project
  `lambda-erp`, owner `lambdadevelopment`, repo `lambda-erp`, workflow
  `release.yml`, environment `release`. The pending publisher lets the **first**
  OIDC run create the project — no token, no manual upload.

### npm — `@lambda-development/erp-core`
- [x] Free org `lambda-development` created (owns the scope).
- [x] **Bootstrap publish** of `0.1.0` done manually — OIDC cannot create a
  brand-new npm package (npm requires it to exist before a trusted publisher can
  be configured).
- [x] **Trusted Publisher enabled** on the package: npmjs.com →
  `@lambda-development/erp-core` → **Settings → Trusted Publisher** → GitHub
  Actions → repository `lambdadevelopment/lambda-erp`, workflow `release.yml`,
  environment `release`. (Confirmed working — `0.1.2` published keylessly.)

### GitHub
- [x] **Environment** named `release` created (repo **Settings → Environments →
  New environment**). It must exist for both registries' OIDC `sub` claim to
  match. Currently empty; add **required reviewers** if you want an approval gate
  (note: a reviewer gate applies to *each* publish job, so you'd approve twice).

---

## Cutting a release (every time)

1. Make sure `master` is green and contains everything for the release.
2. **Update the changelog** — in `CHANGELOG.md`, move the `[Unreleased]` items
   into a new `## [X.Y.Z] - YYYY-MM-DD` section and add the `[X.Y.Z]` compare
   link at the bottom.
3. **Bump the version in lockstep** (both must equal the tag):
   - `pyproject.toml` → `version = "X.Y.Z"`
   - `frontend/package.json` → `"version": "X.Y.Z"`
4. Commit and push to `master`:
   ```bash
   git commit -am "Release vX.Y.Z"
   git push origin master   # also triggers the Azure demo deploy — expected
   ```
5. **Tag and push the tag — this is what publishes:**
   ```bash
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```
6. Watch the **Release** workflow (Actions tab):
   - `verify-version` — fails unless tag (minus `v`) == both package versions.
   - `publish-pypi` — `python -m build` → PyPI (OIDC).
   - `publish-npm` — `npm ci` + `build:lib` → npm (OIDC, with provenance).
   - `github-release` — creates the GitHub Release from the matching
     `CHANGELOG.md` section.
7. **Verify**: <https://pypi.org/project/lambda-erp/>,
   <https://www.npmjs.com/package/@lambda-development/erp-core>, and the repo's
   Releases page all show the new version.

---

## Notes & gotchas

- **The first CI release is `v0.1.1`, not `v0.1.0`.** npm `0.1.0` was already
  published by the manual bootstrap, so a `v0.1.0` tag would make `publish-npm`
  fail on a duplicate version. Start coordinated releases at `0.1.1`.
- **Versions must be in lockstep.** The `verify-version` job hard-fails any tag
  whose number ≠ both package versions.
- **A commit does not publish — pushing a `v*` tag does.** `release.yml` is
  separate from `deploy.yml` (which runs on `master` pushes), so a tag never
  re-deploys the demo.
- **OIDC rejections** are almost always a mismatch between the workflow
  (`release.yml`, environment `release`, repo `lambdadevelopment/lambda-erp`)
  and the registry's trusted-publisher config — or a missing GitHub `release`
  environment. There are no tokens to rotate.
- **Semver the seams.** The extension registries/hooks and the public package
  imports are a public contract — a breaking change there is a major bump.
- **The npm README updates only on a new version publish** (npm refreshes it per
  version), so `frontend/README.md` changes appear with the next release.
