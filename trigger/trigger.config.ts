import { defineConfig } from "@trigger.dev/sdk";

export default defineConfig({
  project: "proj_vvsvdbeeoiaausrkdiqp",
  runtime: "node-24",
  dirs: ["./src"],
  retries: {
    enabledInDev: false,
    default: {
      maxAttempts: 3,
      factor: 2,
      minTimeoutInMs: 5_000,
      maxTimeoutInMs: 60_000,
      randomize: true,
    },
  },
  // Pipeline runs can take a few minutes (discovery + enrichment)
  maxDuration: 600,
});
