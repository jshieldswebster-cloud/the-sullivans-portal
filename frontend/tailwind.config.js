/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx,ts,tsx}"],
  theme: {
    extend: {
      colors: {
        luxe: {
          bg: "#0f0e10",
          surface: "#1a181c",
          accent: "#c9a96e",
          muted: "#8a8580",
          text: "#e6e2da",
        },
      },
      fontFamily: {
        display: ["Georgia", "Times New Roman", "serif"],
        sans: ["Inter", "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
};
