import type { AgentEvent } from "@/lib/api";
import { label } from "@/lib/format";

const STAGES = ["planner", "research", "draft", "execute", "validate", "analyze_failure", "repair", "evaluate"];

/** Which stage runs after the last finished one, following the workflow graph. */
export function nextStage(events: AgentEvent[]): string | null {
  const last = events[events.length - 1];
  if (!last) return "planner";
  switch (last.agent_name) {
    case "planner":
      return "research";
    case "research":
      return "draft";
    case "draft":
    case "repair":
      return "execute";
    case "execute":
      return "validate";
    case "validate":
      return last.event_type === "VALIDATION_PASSED" ? "evaluate" : "analyze_failure";
    case "analyze_failure":
      return "repair"; // or evaluate when the repair budget is spent; the next event settles it
    default:
      return null;
  }
}

/** The workflow as a row of stages: finished ones are green, a failed one red, the running one pulses. */
export function Pipeline({ events, running }: { events: AgentEvent[]; running: boolean }) {
  const active = running ? nextStage(events) : null;
  return (
    <div className="pipeline">
      {STAGES.map((stage, i) => {
        const seen = events.filter((e) => e.agent_name === stage);
        const failed = seen.some((e) => e.status === "error" || (stage !== "validate" && stage !== "execute" && e.status === "failed"));
        const repairsLoop = stage === "analyze_failure" || stage === "repair";
        let cls = "stage";
        if (active === stage) cls += " active";
        else if (failed) cls += " failed";
        else if (seen.length) cls += " done";
        else if (!running && repairsLoop) cls += " skipped";
        return (
          <span key={stage} style={{ display: "contents" }}>
            {i > 0 ? <span className="arrow">›</span> : null}
            <span className={cls}>
              {label(stage)}
              {seen.length > 1 ? ` ×${seen.length}` : ""}
            </span>
          </span>
        );
      })}
    </div>
  );
}
