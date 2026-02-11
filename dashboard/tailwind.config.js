/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './src/pages/**/*.{js,ts,jsx,tsx,mdx}',
    './src/components/**/*.{js,ts,jsx,tsx,mdx}',
    './src/app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        // Trading colors
        profit: {
          DEFAULT: '#22c55e',
          dark: '#16a34a',
        },
        loss: {
          DEFAULT: '#ef4444',
          dark: '#dc2626',
        },
        // ICT concept colors
        fvg: {
          bullish: 'rgba(34, 197, 94, 0.3)',
          bearish: 'rgba(239, 68, 68, 0.3)',
        },
        ob: {
          bullish: 'rgba(59, 130, 246, 0.3)',
          bearish: 'rgba(249, 115, 22, 0.3)',
        },
        liquidity: {
          bsl: 'rgba(168, 85, 247, 0.5)',
          ssl: 'rgba(236, 72, 153, 0.5)',
        },
      },
    },
  },
  plugins: [],
}
