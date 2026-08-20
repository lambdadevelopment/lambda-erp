/**
 * Public entry point for the published @lambda-development/erp-core frontend library.
 *
 * A customer deployment depends on this package, registers its overrides
 * (doctypes, routes, nav, components, branding, API base) in a plugin module,
 * imports the stylesheet, then calls bootstrap(). See the README section
 * "Building a customer deployment on top of the core".
 *
 * Styling: this package follows the "consumer scans source" model. In the
 * customer app:
 *   - import "@lambda-development/erp-core/styles.css"
 *   - add this package to Tailwind `content`:
 *       './node_modules/@lambda-development/erp-core/dist/**\/*.js'
 *   - add the preset: presets: [require("@lambda-development/erp-core/tailwind-preset")]
 */

// App bootstrap
export { bootstrap, createAppQueryClient } from "./bootstrap";
export type { BootstrapOptions } from "./bootstrap";

// App shell (layout: sidebar + header)
export { AppShell } from "./components/layout/app-shell";

// Route registry
export { buildRoutes, registerRoute } from "./routes";

// Doctype registry (generic list/form schema)
export {
  registerDoctype,
  getDoctypeConfig,
  getAllDoctypeConfigs,
} from "./lib/doctypes";
export type {
  DoctypeConfig,
  FieldDef,
  ChildTableDef,
  ConversionDef,
} from "./lib/doctypes";

// Master registry (generic master list/form metadata + registered action UI)
export { registerMaster, getMasterConfig, getAllMasterConfigs } from "./lib/masters";
export type { MasterConfig, MasterFilterDef, MasterActionDef } from "./lib/masters";

// Sidebar navigation registry
export { getNavGroups, registerNavGroup, registerNavItem } from "./lib/nav";
export type { NavGroup, NavItem } from "./lib/nav";

// Component override registry
export { registerComponent, getComponent } from "./lib/component-registry";

// Prev/next record navigation (works on any doctype; custom detail pages can
// drop in <DocPager slug=… name=… onSave=… />). The list-context store lets it
// follow the list the user came from.
// Shared confirm/prompt dialogs (replace window.confirm/prompt). DialogProvider
// is mounted by AppShell; call useConfirm()/usePrompt() anywhere below it.
// ConfirmButton is the inline arm-to-confirm variant for in-row actions.
export { DialogProvider, useConfirm, usePrompt } from "./components/ui/dialog";
export { ConfirmButton } from "./components/ui/confirm-button";

export { DocPager } from "./components/document/doc-pager";
export { setListContext, getListContext } from "./lib/doc-list-context";
export type { ListContext } from "./lib/doc-list-context";

// Branding / theme
export { configureBranding, getBranding } from "./lib/branding";
export type { Branding } from "./lib/branding";

export { usePageTitle } from "./lib/use-page-title";

// Date display locale (default: viewer's browser locale). Pin per deployment,
// e.g. setDateLocale("de-CH").
export { setDateLocale, formatDate } from "./lib/utils";

// API client
export { api, request, ApiError, configureApiBase } from "./api/client";

// Auth
export { AuthProvider, useAuth } from "./contexts/auth-context";
export type { User } from "./contexts/auth-context";

// i18n
export {
  default as i18n,
  SUPPORTED_LANGUAGES,
  getStoredLanguage,
} from "./i18n";
export type { LanguageCode } from "./i18n";
