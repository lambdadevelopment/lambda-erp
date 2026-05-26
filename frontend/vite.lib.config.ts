import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import dts from "vite-plugin-dts";
import path from "path";

// Library build for the published @lambda/erp-core package. Distinct from the
// default vite.config.ts, which builds the demo *app*. This emits an ESM
// bundle + type declarations from src/index.ts and externalizes React and the
// other framework peers so the customer app owns a single copy of each.
export default defineConfig({
  plugins: [
    react(),
    dts({ include: ["src"], insertTypesEntry: true, tsconfigPath: "./tsconfig.json" }),
  ],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  build: {
    lib: {
      entry: path.resolve(__dirname, "src/index.ts"),
      formats: ["es"],
      fileName: () => "index.js",
    },
    outDir: "dist",
    sourcemap: true,
    copyPublicDir: false,
    rollupOptions: {
      external: [
        "react",
        "react/jsx-runtime",
        "react-dom",
        "react-dom/client",
        "react-router-dom",
        "@tanstack/react-query",
        "@tanstack/react-table",
        "react-i18next",
        "i18next",
        "recharts",
        "lucide-react",
      ],
    },
  },
});
