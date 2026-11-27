"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { NewRunForm } from "@/components/NewRunForm";
import { ReplayNotice } from "@/components/Replay";
import { AcceptanceBadge, Banner, Card, StatusBadge } from "@/components/ui";
import { api, REPLAY } from "@/lib/api";
import { fmtNumber, recordedOn, timeAgo } from "@/lib/format";
import { usePolling } from "@/lib/usePolling";

export default function RunsPage() {
  const router = useRouter();
  const { data: runs, error } = usePolling(() => api.runs(50), 3000);

  return (
    <>
      <h1>Runs</h1>
      <p className="subtitle">
        {REPLAY
          ? "Recorded runs, played back step by step. Open one to watch its agents work."
          : "Every run the API has executed. Open one to watch its agents work."}
      </p>
      {error ? <Banner tone="bad">{error.message}</Banner> : null}
      <div className="grid-2">
        <Card title="History">
          {runs === undefined ? (
            <span className="muted">Loading…</span>
          ) : runs.length === 0 ? (
            <span className="muted">{REPLAY ? "No recorded runs in this replay." : "No runs yet. Start one on the right."}</span>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Task</th>
                  <th>Model</th>
                  <th>Status</th>
                  <th>Acceptance</th>
                  <th className="num">Tokens</th>
                  <th className="num">{REPLAY ? "Recorded" : "Started"}</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => (
                  <tr key={run.id} className="clickable" onClick={() => router.push(`/runs/${run.id}`)}>
                    <td>
                      <Link href={`/runs/${run.id}`}>{run.task_id ?? run.task.slice(0, 40)}</Link>
                    </td>
                    <td className="muted">
                      {run.provider}
                      <div className="mono">{run.model}</div>
                    </td>
                    <td>
                      <StatusBadge status={run.status} />
                    </td>
                    <td>
                      <AcceptanceBadge value={run.acceptance_passed} />
                    </td>
                    <td className="num">{fmtNumber(run.total_tokens)}</td>
                    <td className="num muted">{REPLAY ? recordedOn(run.created_at) : timeAgo(run.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
        {REPLAY ? <ReplayNotice /> : <NewRunForm />}
      </div>
    </>
  );
}
