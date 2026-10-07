import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["test/**/*.test.ts"],
    globalSetup: ["test/support/wait-for-ehr.ts"],
    testTimeout: 30_000,
    hookTimeout: 30_000,
  },
});
