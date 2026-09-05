import eslint from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import typescript from "typescript-eslint";

export default typescript.config(
  {
    ignores: ["dist", "node_modules"],
  },
  eslint.configs.recommended,
  ...typescript.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    ...reactHooks.configs.flat["recommended-latest"],
    ...reactRefresh.configs.vite,
  },
);
