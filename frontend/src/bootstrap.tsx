import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import { AuthProvider } from "./contexts/auth-context";
import { buildRoutes } from "./routes";
import { registerChatDoctypeRedirects } from "./lib/chat-doctype-redirects";
import "./i18n"; // initialize i18next (reads saved language from localStorage)

export function createAppQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: 1, refetchOnWindowFocus: false },
    },
  });
}

export interface BootstrapOptions {
  /** Mount target. Defaults to the element with id "root". */
  rootElement?: HTMLElement;
  /** Provide a custom QueryClient (e.g. to tune caching). */
  queryClient?: QueryClient;
}

/**
 * Render the full app (providers + router) into the DOM. The demo app and a
 * customer deployment both call this from their entry module — the customer
 * imports its plugin (which registers overrides/routes/doctypes) and the
 * stylesheet first, then calls bootstrap(). Routes are built here, after
 * registration, so overrides take effect.
 */
export async function bootstrap(opts: BootstrapOptions = {}) {
  const root = opts.rootElement ?? document.getElementById("root");
  if (!root) {
    throw new Error('bootstrap: no mount element (pass rootElement or add <div id="root">)');
  }
  const queryClient = opts.queryClient ?? createAppQueryClient();
  // Auto-register redirects for page-less chat-doctypes (best-effort, bounded)
  // BEFORE routes are built, so their /app/{slug}/{name} links resolve.
  await registerChatDoctypeRedirects();
  const router = createBrowserRouter(buildRoutes());

  createRoot(root).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <RouterProvider router={router} />
        </AuthProvider>
      </QueryClientProvider>
    </StrictMode>,
  );
}
