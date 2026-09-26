"use client";

import { useState } from "react";
import type { AgentEvent, Json, TokenUsage } from "@/lib/api";
import { fmtMs, fmtNumber, label } from "@/lib/format";
import { EventBody, summarize } from "./EventView";

interface StepProps {
  seq: number;
  agent: string;
  eventType: string;
  status: string;
  latencyMs: number;
  usage: TokenUsage;
  output: Record<string, Json>;
  defaultOpen: boolean;
}

function statusTone(status: string): string {
  if (status === "ok") return "ok";
  if (status === "failed" || status === "error") return "bad";
  return "warn"; // e.g. "empty"
}

/** One agent step. Open state is kept per step, so new steps arriving never collapse one you are reading. */
export function Step({ seq, agent, eventType, status, latencyMs, usage, output, defaultOpen }: StepProps) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <details className="step" open={open} onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>
        <span className="seq">{seq}</span>
        <span className="title">{label(agent)}</span>
        <span className={`badge ${statusTone(status)}`}>{eventType.toLowerCase().replaceAll("_", " ")}</span>
        <span className="muted">{summarize(agent, output)}</span>
        <span className="facts">
          {fmtMs(latencyMs)} · {fmtNumber(usage.input + usage.output)} tokens
        </span>
      </summary>
      <div className="step-body">
        <EventBody agent={agent} status={status} output={output} />
      </div>
    </details>
  );
}

export function Timeline({ events }: { events: AgentEvent[] }) {
  return (
    <div className="stack">
      {events.map((e, i) => (
        <Step
          key={e.id}
          seq={e.seq}
          agent={e.agent_name}
          eventType={e.event_type}
          status={e.status}
          latencyMs={e.latency_ms}
          usage={e.token_usage}
          output={e.output}
          defaultOpen={i === events.length - 1 || e.status !== "ok"}
        />
      ))}
    </div>
  );
}
