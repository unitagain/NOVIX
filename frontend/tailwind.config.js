/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class', // Keep class strategy, though we focus on light mode first
  content: [
    "./index.html",
    "./src/**/*.{js,jsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Antigravity Palette
        background: "#F6F0E6", // Warm canvas
        surface: "#FBF6ED",    // Paper surface

        // Text Colors
        ink: {
          900: "#2B2A27", // Primary text
          500: "#6F6A60", // Secondary text
          400: "#8C8579", // Placeholder text
        },

        // Interactive/Accents
        primary: {
          DEFAULT: "#2F6FDB", // Focus/Accent
          hover: "#255CC4",
          light: "#E8DFCE",   // Hover backgrounds
        },

        accent: {
          DEFAULT: "#E8DFCE", // Subtle highlights
          active: "#DCCFB7",  // Active indicators
        },

        // Semantic
        border: "#E0D6C5",      // Subtle borders
        input: "#D8CDBB",       // Input borders
        ring: "#2F6FDB",        // Focus rings
      },
      fontFamily: {
        // UI Fonts: 干净系统无衬线（对齐 Trae）：拉丁/数字优先系统 UI 字体，CJK 跟思源黑体
        sans: [
          '"Segoe UI"',
          'system-ui',
          '-apple-system',
          'Inter',
          '"Source Han Sans SC"',
          '"Noto Sans SC"',
          '"PingFang SC"',
          '"Microsoft YaHei"',
          'sans-serif',
        ],
        // Writing Fonts: Elegant serif
        serif: ['"Source Han Serif SC"', '"Noto Serif SC"', '"Songti SC"', '"STSong"', 'serif'],
        // Code
        mono: ['"JetBrains Mono"', 'monospace'],
      },
      boxShadow: {
        // Minimal separation
        'paper': '0 1px 2px rgba(0, 0, 0, 0.06)',
        'paper-hover': '0 2px 6px rgba(0, 0, 0, 0.08)',
        'float': '0 4px 10px rgba(0, 0, 0, 0.10)',
      },
      animation: {
        'fade-in': 'fadeIn 0.5s ease-out',
        'slide-up': 'slideUp 0.5s ease-out',
      },
      keyframes: {
        fadeIn: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        slideUp: {
          '0%': { transform: 'translateY(10px)', opacity: '0' },
          '100%': { transform: 'translateY(0)', opacity: '1' },
        }
      }
    },
  },
  plugins: [],
}
