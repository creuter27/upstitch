import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from 'tailwindcss'
import autoprefixer from 'autoprefixer'

// On Windows, start.bat copies this file to %VENV%\ and sets VITE_FRONTEND_SRC
// to './frontend-src' (the directory symlink pointing to the Tresorit folder).
// On Mac, VITE_FRONTEND_SRC is not set and '.' resolves relative to frontend/.
const frontendSrc = (process.env.VITE_FRONTEND_SRC ?? '.').replace(/\\/g, '/')

export default defineConfig({
  plugins: [react()],
  cacheDir: process.env.VITE_CACHE_DIR || 'node_modules/.vite',
  resolve: {
    // Keep symlink paths so Rollup resolves node_modules relative to the
    // symlink location (C:\...\frontend-src) rather than the T: target.
    preserveSymlinks: true,
  },
  // Inline PostCSS config so tailwindcss/autoprefixer are imported here,
  // resolved from wherever this config file lives (frontend/ on Mac,
  // %VENV%\ on Windows). This avoids postcss.config.js needing to resolve
  // 'tailwindcss' via CJS require() from the Tresorit virtual drive.
  css: {
    postcss: {
      plugins: [
        tailwindcss({
          content: [
            `${frontendSrc}/index.html`,
            `${frontendSrc}/src/**/*.{ts,tsx}`,
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
        }),
        // overrideBrowserslist bypasses config-file discovery, which avoids the
        // "contains both .browserslistrc and browserslist" error thrown when
        // autoprefixer processes @xterm/xterm/css/xterm.css (that package ships
        // with conflicting browserslist configs in the same directory).
        autoprefixer({ overrideBrowserslist: ['>0.5%', 'last 2 versions', 'not dead'] }),
      ],
    },
  },
  server: {
    host: '127.0.0.1',
    watch: {
      usePolling: true,
      interval: 1000,
    },
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/ws': {
        target: 'ws://127.0.0.1:8000',
        ws: true
      }
    }
  },
  build: {
    outDir: 'dist'
  }
})
