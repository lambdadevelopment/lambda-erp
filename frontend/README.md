# @lambda-development/erp-core

The frontend core of **Lambda ERP**, an AI-native ERP, packaged as a library:
the app shell (sidebar + header + router), the generic document/master pages,
the chat UI, the reports, and the registries that let a customer deployment
extend or override any of it **without forking**.

- Backend companion package: **`lambda-erp`** on PyPI.
- Source, docs & changelog: <https://github.com/lambdadevelopment/lambda-erp>

## Install

```bash
npm install @lambda-development/erp-core
```

Install the peer dependencies in your app:

```bash
npm install react react-dom react-router-dom \
  @tanstack/react-query @tanstack/react-table \
  i18next react-i18next recharts lucide-react
```

## Quick start

```ts
import {
  bootstrap,
  configureApiBase,
  configureBranding,
} from "@lambda-development/erp-core";
import "@lambda-development/erp-core/styles.css";

configureApiBase("https://erp.acme.example/api"); // defaults to "/api"
configureBranding({ appName: "Acme ERP", logoUrl: "/acme.svg" });

// ...register your overrides (see below)...

bootstrap(); // mounts the app shell + router into #root
```

## Styling (Tailwind — "consumer scans source")

Your app runs Tailwind, scans the library's compiled output for classes, and
pulls the shared design tokens from the exported preset:

```js
// tailwind.config.js
module.exports = {
  presets: [require("@lambda-development/erp-core/tailwind-preset")],
  content: [
    "./src/**/*.{ts,tsx}",
    "./node_modules/@lambda-development/erp-core/dist/**/*.js",
  ],
};
```

Import the base stylesheet once: `import "@lambda-development/erp-core/styles.css";`

## Extending the core

Every seam is an additive registry — call it **before** `bootstrap()`:

| Export | Purpose |
|--------|---------|
| `registerDoctype(config)` | Add or override a document type's list/form schema |
| `registerRoute(route)` | Add or override a route (merge-by-path) |
| `registerNavGroup` / `registerNavItem` | Extend the sidebar |
| `registerComponent(name, Component)` | Swap a core component (e.g. the dashboard) |
| `configureBranding({ appName, logoUrl, tokens })` | App name, logo, theme tokens |
| `configureApiBase(url)` | Point the client at your backend |

Also exported: `AppShell`, `buildRoutes`, `getDoctypeConfig` /
`getAllDoctypeConfigs`, the `api` client (`request`, `ApiError`),
`AuthProvider` / `useAuth`, the `i18n` instance + `SUPPORTED_LANGUAGES`, and the
related TypeScript types (`DoctypeConfig`, `FieldDef`, `BootstrapOptions`, …).

See **[Building a customer deployment on top of the core](https://github.com/lambdadevelopment/lambda-erp/blob/master/docs/core-extension-architecture.md)**
for the full extension architecture.

## License

Apache License 2.0. See the [repository LICENSE](https://github.com/lambdadevelopment/lambda-erp/blob/master/LICENSE).
