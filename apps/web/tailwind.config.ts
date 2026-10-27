import type { Config } from "tailwindcss";

export default {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // PulseOps command-center palette
        ink: {
          950: "#07090d", // page background
          900: "#0b0e14", // panel background
          850: "#10141d", // card background
          800: "#161b26", // raised card
          700: "#212836", // borders
          600: "#2e3747",
        },
        pulse: {
          400: "#38e1c6", // primary accent (teal)
          500: "#14c8ad",
        },
        risk: {
          low: "#34d399",
          mid: "#fbbf24",
          high: "#fb7185",
        },
      },
      fontFamily: {
        display: ["Inter", "system-ui", "sans-serif"],
        mono: ["'JetBrains Mono'", "ui-monospace", "SFMono-Regular", "monospace"],
      },
    },
  },
  plugins: [],
} satisfies Config;
