// Read-only replay of recorded runs, for the static demo site.
//
// `python -m agenteval.dev.replay` exports finished runs from a running API into public/replay/. A replay build
// (NEXT_PUBLIC_REPLAY=1) serves those files instead of calling the API, and plays each run back step by step so
// the run page shows the agents working, as it would live. Steps keep their recorded order and relative length;
// a whole run is compressed to about PLAYBACK_MS.

import type { AgentEvent, Failure, Metric, Run, Task } from "./api";
import { ApiError } from "./errors";

export const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH ?? "";
export const REPO_URL = process.env.NEXT_PUBLIC_REPO_URL ?? "https://github.com/sampro14/agentval_system";

const PLAYBACK_MS = 25_000; // the longest a full run takes to play back
const MIN_STEP_MS = 700; // so short steps are still visible

interface Bundle {
  run: Run;
  trajectory: AgentEvent[];
  failures: Failure[];
  evaluation: Metric[];
}

interface Playback {
  bundle: Bundle;
  offsets: number[]; // when each event appears, in ms after the playback starts
  speedup: number;
}

const cache = new Map<string, Promise<unknown>>();
const playbacks = new Map<string, Promise<Playback>>();
const clocks = new Map<string, number>(); // run id -> when its playback started (epoch ms)

async function file<T>(path: string): Promise<T> {
  if (!cache.has(path)) {
    cache.set(
      path,
      fetch(`${BASE_PATH}/replay/${path}`).then(async (response) => {
        if (!response.ok) throw new ApiError(`This replay has no recorded data at ${path}.`, response.status);
        return response.json();
      }),
    );
  }
  return cache.get(path) as Promise<T>;
}

function schedule(events: AgentEvent[]): { offsets: number[]; speedup: number } {
  const recorded = events.reduce((sum, e) => sum + Math.max(0, e.latency_ms), 0);
  const scale = recorded > PLAYBACK_MS ? PLAYBACK_MS / recorded : 1;
  let t = 0;
  const offsets = events.map((e) => (t += Math.max(MIN_STEP_MS, e.latency_ms * scale)));
  return { offsets, speedup: t > 0 ? recorded / t : 1 };
}

function playback(id: string): Promise<Playback> {
  if (!playbacks.has(id)) {
    playbacks.set(
      id,
      file<Bundle>(`runs/${id}.json`).then((bundle) => {
        const trajectory = [...bundle.trajectory].sort((a, b) => a.seq - b.seq);
        return { bundle: { ...bundle, trajectory }, ...schedule(trajectory) };
      }),
    );
  }
  return playbacks.get(id) as Promise<Playback>;
}

function elapsed(id: string): number {
  if (!clocks.has(id)) clocks.set(id, Date.now());
  return Date.now() - (clocks.get(id) as number);
}

const lastOffset = (p: Playback) => p.offsets[p.offsets.length - 1] ?? 0;

/** Playback state for the run page's controls. */
export async function replayInfo(id: string): Promise<{ done: boolean; speedup: number; recordedAt: string }> {
  const p = await playback(id);
  return { done: elapsed(id) >= lastOffset(p), speedup: p.speedup, recordedAt: p.bundle.run.created_at };
}

/** Jump to the end of a run's playback; the run page picks it up on its next poll. */
export function skipToEnd(id: string): void {
  clocks.set(id, 0);
}

/** Start a run's playback again from the first step. */
export function restart(id: string): void {
  clocks.delete(id);
}

const readOnly = (): never => {
  throw new ApiError("This is a read-only replay of recorded runs. Clone the repo to start your own.", 405);
};

export const replayApi = {
  runs: async (limit = 50) => (await file<Run[]>("runs.json")).slice(0, limit),

  run: async (id: string): Promise<Run> => {
    const p = await playback(id);
    const t = elapsed(id);
    if (t >= lastOffset(p)) return p.bundle.run;
    // Still playing: show the run as it looked while it was running.
    return {
      ...p.bundle.run,
      status: "running",
      started_at: new Date(clocks.get(id) as number).toISOString(),
      completed_at: null,
      total_tokens: null,
      total_latency_ms: null,
      final_score: null,
      acceptance_passed: null,
    };
  },

  trajectory: async (id: string, afterSeq = 0): Promise<AgentEvent[]> => {
    const p = await playback(id);
    const t = elapsed(id);
    return p.bundle.trajectory.filter((e, i) => p.offsets[i] <= t && e.seq > afterSeq);
  },

  failures: async (id: string): Promise<Failure[]> => (await playback(id)).bundle.failures,
  evaluation: async (id: string): Promise<Metric[]> => (await playback(id)).bundle.evaluation,
  tasks: () => file<Task[]>("tasks.json"),
  providers: async () => [],
  createRun: readOnly,
  stageStatus: readOnly,
  stageResult: readOnly,
  runStage: readOnly,
};
