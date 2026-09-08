import type { Config } from "tailwindcss";

/** Tailwind reads the CSS custom properties in globals.css so tokens have
 *  exactly one source of truth. Never hard-code a hex in a component. */
export default {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: "var(--canvas)",
        surface: {
          DEFAULT: "var(--surface)",
          subtle: "var(--surface-subtle)",
          sunken: "var(--surface-sunken)",
          inset: "var(--surface-inset)",
        },
        hairline: {
          DEFAULT: "var(--border)",
          strong: "var(--border-strong)",
          loud: "var(--border-loud)",
        },
        ink: {
          DEFAULT: "var(--ink)",
          body: "var(--ink-body)",
          muted: "var(--ink-muted)",
          subtle: "var(--ink-subtle)",
          faint: "var(--ink-faint)",
        },
        accent: {
          DEFAULT: "var(--accent)",
          hover: "var(--accent-hover)",
          active: "var(--accent-active)",
          wash: "var(--accent-wash)",
          "wash-strong": "var(--accent-wash-strong)",
        },
        critical: { DEFAULT: "var(--critical)", wash: "var(--critical-wash)" },
        high: { DEFAULT: "var(--high)", wash: "var(--high-wash)" },
        medium: { DEFAULT: "var(--medium)", wash: "var(--medium-wash)" },
        low: { DEFAULT: "var(--low)", wash: "var(--low-wash)" },
        info: { DEFAULT: "var(--info)", wash: "var(--info-wash)" },
        ok: { DEFAULT: "var(--ok)", wash: "var(--ok-wash)" },
      },
      fontFamily: {
        sans: "var(--font-sans)",
        mono: "var(--font-mono)",
      },
      borderRadius: { DEFAULT: "var(--radius)", sm: "var(--radius-sm)" },
      boxShadow: {
        1: "var(--shadow-1)",
        2: "var(--shadow-2)",
        overlay: "var(--shadow-overlay)",
      },
      spacing: { sidebar: "var(--sidebar-w)", header: "var(--header-h)" },
    },
  },
  plugins: [],
} satisfies Config;
