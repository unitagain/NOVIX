/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: [
    "./index.html",
    "./src/**/*.{js,jsx}",
  ],
  theme: {
    extend: {
      colors: {
        background: "#050505", // Deep black
        surface: "#0A0A0A", // Slightly lighter black
        card: "#121212", // Card background
        border: "#27272A", // Zinc 800
        primary: {
          DEFAULT: "#00FF94", // Neon Green
          hover: "#00CC76",
          foreground: "#000000",
        },
        secondary: {
          DEFAULT: "#27272A", // Zinc 800
          foreground: "#FFFFFF",
        },
        muted: {
          DEFAULT: "#71717A", // Zinc 500
          foreground: "#A1A1AA", // Zinc 400
        },
        destructive: {
          DEFAULT: "#FF4545",
          foreground: "#FFFFFF",
        }
      },
      fontFamily: {
        sans: ['"Noto Sans SC"', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'monospace'],
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
      }
    },
  },
  plugins: [],
}
