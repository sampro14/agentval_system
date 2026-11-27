"use client";

import { useEffect, useState } from "react";
import { Card } from "@/components/ui";
import { REPO_URL, replayInfo, restart, skipToEnd } from "@/lib/replay";

/** Replaces the "start a run" form on a replay build. */
export function ReplayNotice() {
  return (
    <Card title="About this replay">
      <div>
        <p style={{ margin: "0 0 10px" }}>
          These are recorded AgentEval runs against the <span className="mono">authkit</span> benchmark app: real model
          calls and a real Docker sandbox, played back step by step. Nothing runs here, so you can&apos;t start a new
          run. Open a run to watch its agents plan, code, test and repair.
        </p>
        <p style={{ margin: "0 0 10px" }}>
          On the held-out fault suite, the validator caught 15 of 15 planted-bug runs with 0 of 9 false alarms, and the
          failure analyzer blamed the right agent in 12 of 15.{" "}
          <a href={`${REPO_URL}#results`} target="_blank" rel="noopener">
            Results and caveats
          </a>
        </p>
        <p className="muted" style={{ margin: 0 }}>
          To run it yourself, clone the repo and follow the{" "}
          <a href={`${REPO_URL}#quick-start`} target="_blank" rel="noopener">
            quick start
          </a>
          .
        </p>
      </div>
    </Card>
  );
}

/** Playback controls on a replayed run. */
export function ReplayBar({ id, running }: { id: string; running: boolean }) {
  const [info, setInfo] = useState<{ speedup: number; recordedAt: string }>();
  useEffect(() => {
    replayInfo(id)
      .then(setInfo)
      .catch(() => undefined);
  }, [id]);
  if (!info) return null;
  const date = new Date(info.recordedAt).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
  return (
    <div className="banner info row" style={{ justifyContent: "space-between" }}>
      <span>
        {running
          ? `Replaying a run recorded on ${date}${info.speedup >= 1.5 ? `, at about ${Math.round(info.speedup)}× speed` : ""}.`
          : `Recorded on ${date}.`}
      </span>
      {running ? (
        <button type="button" onClick={() => skipToEnd(id)}>
          Skip to the end
        </button>
      ) : (
        <button
          type="button"
          onClick={() => {
            restart(id);
            window.location.reload();
          }}
        >
          Replay from the start
        </button>
      )}
    </div>
  );
}
