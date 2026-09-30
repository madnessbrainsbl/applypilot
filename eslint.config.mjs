import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { FlatCompat } from "@eslint/eslintrc";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const compat = new FlatCompat({ baseDirectory: __dirname });

const eslintConfig = [
  // autopilot/ and tools/ are the Python side; autopilot/chrome-profile in particular
  // holds bundled extension JS, which `eslint .` was linting (1149 errors) and failing on
  { ignores: [".next/", "coverage/", "__mocks__/", "autopilot/", "tools/"] },
  ...compat.extends("next/core-web-vitals"),
  {
    // Playwright's fixture `use` callback is not a React hook.
    files: ["e2e/**"],
    rules: { "react-hooks/rules-of-hooks": "off" },
  },
];

export default eslintConfig;
