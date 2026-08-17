/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#1D1D1F",
        "ink-soft": "#6E6E73",
        "ink-faint": "#8A8A8E",
        canvas: "#F7F7F8",
        line: "#ECECEE",
        teal: {
          DEFAULT: "#0F7A6B",
          soft: "#EAF6F1",
        },
        bronze: { DEFAULT: "#A9764F", soft: "#FBF3EC" },
        silver: { DEFAULT: "#8E8E93", soft: "#F4F4F5" },
        gold: { DEFAULT: "#B8952E", soft: "#FBF6E8" },
        success: { DEFAULT: "#2FA37E", soft: "#EAF6F1" },
        running: { DEFAULT: "#3E7BD6", soft: "#EAF1FB" },
        danger: { DEFAULT: "#D6483E", soft: "#FBEAEA" },
      },
      fontFamily: {
        sans: ["-apple-system", "BlinkMacSystemFont", "SF Pro Text", "Inter", "sans-serif"],
        mono: ["ui-monospace", "SF Mono", "IBM Plex Mono", "monospace"],
      },
      borderRadius: {
        card: "16px",
      },
    },
  },
  plugins: [],
};
