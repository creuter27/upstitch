/** @type {import('tailwindcss').Config} */
export default {
  content: [
    './index.html',
    './src/**/*.{ts,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        'vscode-bg': '#1e1e1e',
        'vscode-sidebar': '#252526',
        'vscode-panel': '#2d2d2d',
        'vscode-border': '#3e3e3e',
        'vscode-text': '#d4d4d4',
        'vscode-muted': '#858585',
        'vscode-accent': '#007acc',
        'vscode-accent-hover': '#1a8cdf',
        'vscode-tab-active': '#1e1e1e',
        'vscode-tab-inactive': '#2d2d2d',
        'vscode-selection': '#264f78',
        'vscode-hover': '#2a2d2e',
      },
    },
  },
  plugins: [],
}
