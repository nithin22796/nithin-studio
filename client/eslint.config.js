import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import prettier from "eslint-config-prettier";
import tseslint from "typescript-eslint";
import { globalIgnores } from "eslint/config";
import globals from "globals";

export default tseslint.config([
  globalIgnores(["dist"]),
  {
    files: ["**/*.{ts,tsx}"],
    extends: [js.configs.recommended, tseslint.configs.recommended, reactRefresh.configs.vite, prettier],
    plugins: {
      "react-hooks": reactHooks,
    },
    rules: reactHooks.configs["recommended-latest"].rules,
    languageOptions: {
      ecmaVersion: 2023,
      globals: globals.browser,
    },
  },
]);
