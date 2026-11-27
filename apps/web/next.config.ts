import type { NextConfig } from "next";

// The dashboard talks to the AgentEval API directly from the browser (see NEXT_PUBLIC_API_URL in lib/api.ts).
// A replay build (NEXT_PUBLIC_REPLAY=1) is a static export of recorded runs, served from NEXT_PUBLIC_BASE_PATH.
const replay = process.env.NEXT_PUBLIC_REPLAY === "1";

const config: NextConfig = replay
  ? { output: "export", basePath: process.env.NEXT_PUBLIC_BASE_PATH ?? "", trailingSlash: true }
  : {};

export default config;
