import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: ["class", '[data-theme="dark"]'],
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // CSS custom properties are set in globals.css by theme, controlled by :root,
        // @media (prefers-color-scheme), and [data-theme] attributes.
        // This allows the tokens to respond to user choice, OS preference, and explicit toggles.
        paper: "var(--color-paper)",
        surface: "var(--color-surface)",
        border: "var(--color-border)",
        "border-soft": "var(--color-border-soft)",
        accent: "var(--color-accent)",

        // Text hierarchy
        ink: {
          DEFAULT: "var(--color-ink)",
          soft: "var(--color-ink-soft)",
          muted: "var(--color-ink-muted)",
          faint: "var(--color-ink-faint)",
        },

        // Status semantic colors
        ok: "var(--color-ok)",
        "ok-bg": "var(--color-ok-bg)",
        warn: "var(--color-warn)",
        "warn-bg": "var(--color-warn-bg)",
        crit: "var(--color-crit)",
        "crit-bg": "var(--color-crit-bg)",
        info: "var(--color-info)",
        "info-bg": "var(--color-info-bg)",

        // Legacy aliases for transition
        positive: "var(--color-ok)",
        caution: "var(--color-warn)",
        critical: "var(--color-crit)",
      },
      fontFamily: {
        sans: ["var(--font-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
        serif: ["var(--font-serif)", "Georgia", "serif"],
      },
      boxShadow: {
        card: "var(--shadow-card)",
        lift: "var(--shadow-lift)",
      },
      borderRadius: {
        xl: "14px",
        "2xl": "20px",
      },
      animation: {
        pulse: "pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite",
      },
      keyframes: {
        pulse: {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.5" },
        },
      },
    },
  },
  plugins: [],
};
export default config;
