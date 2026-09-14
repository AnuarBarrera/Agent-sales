/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#10212b",
        canvas: "#f5f8fa",
        teal: {
          50: "#effcf9",
          100: "#d7f5ef",
          500: "#0f9f8f",
          600: "#087f73",
          700: "#07645d",
        },
        amber: { 100: "#fff1c7", 500: "#e8a11a", 700: "#95610b" },
      },
      boxShadow: {
        soft: "0 14px 40px rgba(16, 33, 43, 0.07)",
      },
    },
  },
  plugins: [],
};
