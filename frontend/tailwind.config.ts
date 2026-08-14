import type { Config } from "tailwindcss";

const config: Config = {
  // Opt out of the default `media` strategy. Three pages (tax, behavioural,
  // regulatory-countdown) carry ~249 stray `dark:` utilities that would otherwise
  // activate from the OS preference and render their text unreadably (1.03:1 contrast
  // on headings). Nothing adds a `dark` class, so those utilities stay inert until
  // real dark mode is built across every page.
  darkMode: "class",
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Institutional palette — calm, trustworthy, with a restrained gold accent.
        ink: {
          DEFAULT: "#0f1d29",
          soft: "#26384a",
          muted: "#5d6f80",
        },
        navy: {
          50: "#f2f6f9",
          100: "#e3edf3",
          200: "#c2d6e3",
          400: "#5d86a3",
          600: "#2a5575",
          700: "#1d4663",
          800: "#163a52",
          900: "#0f2b3d",
        },
        gold: {
          DEFAULT: "#c8a35e",
          soft: "#e3cd9d",
          dark: "#a9853f",
        },
        paper: "#f7f8fa",
        surface: "#ffffff",
        positive: "#1f7a55",
        caution: "#b9852b",
        critical: "#b23b3b",
      },
      fontFamily: {
        sans: ["var(--font-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
        serif: ["var(--font-serif)", "Georgia", "serif"],
      },
      boxShadow: {
        card: "0 1px 2px rgba(15,29,41,0.04), 0 8px 24px -12px rgba(15,29,41,0.12)",
        lift: "0 12px 40px -16px rgba(15,29,41,0.28)",
      },
      borderRadius: {
        xl: "14px",
        "2xl": "20px",
      },
    },
  },
  plugins: [],
};
export default config;
