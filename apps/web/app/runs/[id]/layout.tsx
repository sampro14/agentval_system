import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { ReactNode } from "react";

/** A replay build pre-renders one page per recorded run; a normal build renders run pages on demand. */
export function generateStaticParams(): { id: string }[] {
  if (process.env.NEXT_PUBLIC_REPLAY !== "1") return [];
  const runs: { id: string }[] = JSON.parse(readFileSync(join(process.cwd(), "public/replay/runs.json"), "utf8"));
  return runs.map((run) => ({ id: run.id }));
}

export default function RunLayout({ children }: { children: ReactNode }) {
  return children;
}
