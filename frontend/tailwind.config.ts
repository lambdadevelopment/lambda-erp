import type { Config } from "tailwindcss";
import erpPreset from "./tailwind.preset";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  presets: [erpPreset as Config],
  plugins: [],
} satisfies Config;
