module.exports = {
  root: true,
  parser: "@typescript-eslint/parser",
  parserOptions: { ecmaVersion: "latest", sourceType: "module", ecmaFeatures: { jsx: true } },
  plugins: ["@typescript-eslint", "react-hooks", "react-refresh"],
  extends: ["eslint:recommended", "plugin:@typescript-eslint/recommended", "plugin:react-hooks/recommended"],
  env: { browser: true, es2022: true, node: true },
  rules: {
    "react-refresh/only-export-components": "off",
    "@typescript-eslint/no-explicit-any": "warn",
  },
};
