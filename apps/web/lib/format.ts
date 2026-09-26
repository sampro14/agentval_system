export function fmtMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "–";
  if (ms < 1000) return `${ms} ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  return `${Math.floor(seconds / 60)} m ${Math.round(seconds % 60)} s`;
}

export function fmtNumber(n: number | null | undefined): string {
  return n === null || n === undefined ? "–" : n.toLocaleString("en-US");
}

export function timeAgo(iso: string, now = Date.now()): string {
  const seconds = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (seconds < 5) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

export function elapsedMs(start: string | null, end: string | null, now = Date.now()): number | null {
  if (!start) return null;
  return (end ? new Date(end).getTime() : now) - new Date(start).getTime();
}

export const AGENT_LABELS: Record<string, string> = {
  planner: "Planner",
  research: "Research",
  draft: "Coder",
  execute: "Executor",
  validate: "Validator",
  analyze_failure: "Failure analyzer",
  repair: "Repairer",
  evaluate: "Evaluation",
};

export const label = (agent: string) => AGENT_LABELS[agent] ?? agent;
