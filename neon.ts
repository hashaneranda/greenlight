import { defineConfig } from "@neon/config/v1";

// Preview branches expire after a week so the free plan never fills up.
export default defineConfig({
  auth: false,
  branch: (branch) => (!branch.isDefault && !branch.exists ? { ttl: "7d" } : {}),
});
