import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      // Token-backed colours. Values come from CSS variables in index.css,
      // which means swapping themes (e.g. dark mode later) means changing
      // the variables, not editing every component.
      //
      // Using `hsl(var(--x) / <alpha-value>)` lets Tailwind opacity
      // modifiers work: `bg-brand/90`, `text-fg-muted/70`, etc.
      colors: {
        brand: {
          DEFAULT: "hsl(var(--brand) / <alpha-value>)",
          fg: "hsl(var(--brand-fg) / <alpha-value>)",
        },
        surface: {
          DEFAULT: "hsl(var(--surface) / <alpha-value>)",
          muted: "hsl(var(--surface-muted) / <alpha-value>)",
          subtle: "hsl(var(--surface-subtle) / <alpha-value>)",
        },
        fg: {
          DEFAULT: "hsl(var(--text) / <alpha-value>)",
          muted: "hsl(var(--text-muted) / <alpha-value>)",
        },
        line: "hsl(var(--border) / <alpha-value>)",
      },
      fontFamily: {
        sans: [
          "Inter",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "sans-serif",
        ],
      },
      boxShadow: {
        // Multi-layer soft shadows — closer in feel to Linear / Vercel
        // than Tailwind's default. The card-hover variant is for
        // interactive cards (hover, focus-within).
        card: "0 1px 2px rgba(0,0,0,0.04), 0 4px 12px rgba(0,0,0,0.03)",
        "card-hover": "0 1px 2px rgba(0,0,0,0.05), 0 8px 24px rgba(0,0,0,0.05)",
        // Subtle inset highlight for primary buttons — gives the
        // top edge a 1px lift that's almost invisible but reads as depth.
        "button-highlight": "inset 0 1px 0 rgba(255,255,255,0.16)",
        // Saturated colored surfaces (user chat bubble, brand CTAs) need
        // a different shadow recipe than neutral cards: a black drop
        // shadow alone barely registers on a vivid purple. Combining
        //   1) a 1px inset top highlight   — reads as polished glass,
        //   2) a tight dark drop           — defines the bubble edge,
        //   3) a soft brand-tinted glow    — makes the bubble feel like
        //      it's casting its own colour onto the page (kept subtle),
        // gives the bubble physical presence without looking gimmicky.
        "bubble-user": [
          "inset 0 1px 0 rgba(255,255,255,0.22)",
          "0 1px 2px rgba(0,0,0,0.05)",
          "0 3px 8px -2px hsl(var(--brand) / 0.18)",
        ].join(", "),
      },
    },
  },
  plugins: [],
} satisfies Config;
