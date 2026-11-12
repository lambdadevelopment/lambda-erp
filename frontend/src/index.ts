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
export { MentionEditor, mentionAction } from "./components/mentions";
export type { MentionUser } from "./components/mentions";
export { RecordNotes, RecordNotesLink } from "./components/record-notes";
export type { RecordNote } from "./components/record-notes";
export { RecordAttachments } from "./components/record-attachments";

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
// SaveButton shows Save / Saving… / ✓ Saved from a form's state; pair it with
// useUnsavedChanges(), which tracks edits and guards leaving with unsaved ones.
export { SaveButton } from "./components/ui/save-button";
export { useUnsavedChanges } from "./lib/use-unsaved-changes";

export { DocPager } from "./components/document/doc-pager";
export { setListContext, getListContext } from "./lib/doc-list-context";
export type { ListContext } from "./lib/doc-list-context";

// Branding / theme
export { configureBranding, getBranding } from "./lib/branding";
export type { Branding } from "./lib/branding";

export { usePageTitle } from "./lib/use-page-title";

// Date display locale (default: viewer's browser locale). Pin per deployment,
// e.g. setDateLocale("de-CH").
export { setDateLocale, formatDate, formatLocalDate } from "./lib/utils";

// API client
export { api, request, ApiError, configureApiBase } from "./api/client";

// Auth
export { AuthProvider, useAuth } from "./contexts/auth-context";
export type { User } from "./contexts/auth-context";
// What a restricted user (access profile, record scope) may open: for leaving
// out links and buttons. The server enforces it regardless.
export { useAccess, accessOf } from "./lib/access";
export type { Access } from "./lib/access";
// Extra columns in the stock view (Stock by Location & Batch).
export { registerStockColumn, getStockColumns } from "./lib/stock-columns";
export type { StockColumn } from "./lib/stock-columns";

// i18n
export {
  default as i18n,
  SUPPORTED_LANGUAGES,
  getStoredLanguage,
} from "./i18n";
export type { LanguageCode } from "./i18n";
