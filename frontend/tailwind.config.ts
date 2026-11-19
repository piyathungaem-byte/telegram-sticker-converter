import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        bg: {
          DEFAULT: "#0d1214",
          elevated: "#12191b",
        },
        panel: {
          DEFAULT: "#161f22",
          elevated: "#1e2a2d",
        },
        line: {
          DEFAULT: "#263538",
          strong: "#384c50",
        },
        mint: {
          DEFAULT: "#38d69d",
          hover: "#58e6b1",
          dim: "#1e4539",
        },
        accent: {
          telegram: "#2aabee",
          whatsapp: "#25d366",
        },
      },
    },
  },
  plugins: [],
};
export default config;
