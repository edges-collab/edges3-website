import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      globals: globals.browser,
    },
    // The recommended rules from react-hooks are too aggressive for our
    // async-data-fetch patterns (set state on mount, poll for updates, etc.).
    // Disable the rules that flag those patterns while keeping the genuinely
    // useful ones (exhaustive-deps, rules-of-hooks, etc.) active.
    rules: {
      'react-hooks/set-state-in-effect': 'off',
      'react-hooks/purity': 'off',
      // Allow non-component exports in files that also export components.
      'react-refresh/only-export-components': 'off',
    },
  },
])
