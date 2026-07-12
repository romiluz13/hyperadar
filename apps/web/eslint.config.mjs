import { defineConfig, globalIgnores } from "eslint/config";
import { FlatCompat } from "@eslint/eslintrc";

const compatibility = new FlatCompat({ baseDirectory: import.meta.dirname });

export default defineConfig([
	...compatibility.config({
		extends: ["next/core-web-vitals", "next/typescript"],
	}),
	globalIgnores([".next/**", "out/**", "build/**", "next-env.d.ts"]),
]);
