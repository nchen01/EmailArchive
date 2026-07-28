/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  // Theme is driven by data-theme on <html> (see src/theme.ts). Both the
  // explicit `dark:` variant and the semantic color tokens below resolve
  // through CSS custom properties, so components flip automatically.
  darkMode: ["selector", '[data-theme="dark"]'],
  theme: {
    extend: {
      fontFamily: {
        display: "var(--font-display)",
        mono: "var(--font-mono)",
      },
      colors: {
        // Surfaces + ink (semantic, theme-responsive)
        surface: "var(--surface)",
        surface2: "var(--surface-2)",
        app: "var(--paper)",
        app2: "var(--surface-2)",
        ink: "var(--text)",
        muted: "var(--muted)",
        faint: "var(--faint)",
        line: { DEFAULT: "var(--line)", 2: "var(--line-2)" },
        line2: "var(--line-2)",
        // The dark "seal band" (recipient package header) — dark in both themes
        band: "var(--band)",
        onband: "var(--onband)",
        // Text on the solid brass accent: flips (white in light, dark ink in dark)
        onbrass: "var(--btn-fg)",
        // Accent + semantics
        brass: { DEFAULT: "var(--brass)", soft: "var(--brass-soft)" },
        jade: { DEFAULT: "var(--jade)", soft: "var(--jade-soft)" },
        warn: { DEFAULT: "var(--warn)", soft: "var(--warn-soft)", line: "var(--warn-line)" },
        danger: { DEFAULT: "var(--danger)", soft: "var(--danger-soft)", line: "var(--danger-line)" },
      },
    },
  },
  plugins: [],
};
