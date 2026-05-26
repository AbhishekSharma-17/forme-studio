import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        paper: {
          50: "#fdfcf9",
          100: "#faf8f2",
          200: "#f3efe5",
          300: "#e8e2d2",
          400: "#cfc6b1",
          500: "#a89e85",
        },
        ink: {
          50: "#f5f5f4",
          100: "#e7e5e4",
          200: "#d6d3d1",
          300: "#a8a29e",
          400: "#78716c",
          500: "#57534e",
          600: "#44403c",
          700: "#292524",
          800: "#1c1917",
          900: "#0c0a09",
        },
        clay: {
          50: "#fef6f1",
          100: "#fce9dd",
          200: "#f8cdb5",
          300: "#f1a47e",
          400: "#e87a4b",
          500: "#d75827",
          600: "#bb3f12",
          700: "#922f0f",
          800: "#74270f",
          900: "#5e220f",
        },
        sage: {
          50: "#f3f6f3",
          100: "#e3eae3",
          200: "#c7d6c7",
          300: "#9eb89e",
          400: "#74997a",
          500: "#577d60",
          600: "#42624a",
          700: "#36503d",
          800: "#2d4032",
          900: "#27352b",
        },
      },
      fontFamily: {
        sans: [
          "var(--font-inter)",
          "ui-sans-serif",
          "system-ui",
          "sans-serif",
        ],
        serif: ["var(--font-fraunces)", "ui-serif", "Georgia", "serif"],
        mono: [
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "monospace",
        ],
      },
      boxShadow: {
        card: "0 1px 2px rgba(12, 10, 9, 0.04), 0 8px 24px -12px rgba(12, 10, 9, 0.06)",
        "card-hover":
          "0 1px 2px rgba(12, 10, 9, 0.06), 0 12px 28px -10px rgba(12, 10, 9, 0.10)",
      },
    },
  },
  plugins: [],
};

export default config;
