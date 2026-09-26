// Typed client for the AgentEval API. The browser calls it directly (CORS is enabled on the API side).

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8001";

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Json = any; // event outputs are free-form JSON produced by the agents

export interface Run {
  id: string;
  task: string;
  task_id: string | null;
  workflow: string;
  provider: string | null;
  model: string | null;
  status: string;
  error: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  total_tokens: number | null;
  total_latency_ms: number | null;
  final_score: number | null;
  acceptance_passed: number | null;
}

export interface TokenUsage {
  input: number;
  output: number;
  llm_calls: number;
}

export interface AgentEvent {
  id: string;
  seq: number;
  agent_name: string;
  event_type: string;
  status: string;
  output: Record<string, Json>;
  latency_ms: number;
  token_usage: TokenUsage;
  created_at: string;
}

export interface Failure {
  id: string;
  stage: string;
  category: string;
  root_cause: string;
  confidence: number;
  evidence: string[];
  recommended_action: string | null;
  attribution_method: string | null;
  resolved: boolean;
}

export interface Metric {
  metric: string;
  score: number;
  evaluator: string;
}

export interface Task {
  id: string;
  title: string;
  difficulty: string;
  tags: string[];
  task: string;
  requirements: string[];
}

export interface Provider {
  name: string;
  default_model: string;
  configured: boolean;
  key_env_vars: string[];
}

export interface StageRow {
  agent: string;
  ran: boolean;
  ok: boolean | null;
  event_type: string | null;
  latency_ms: number | null;
  tokens_in: number | null;
  tokens_out: number | null;
}

export interface Exchange {
  system: string;
  prompt: string;
  response: string;
  input_tokens: number;
  output_tokens: number;
  model: string;
}

export interface StageResult {
  agent: string;
  task_id: string;
  ok: boolean;
  error: string | null;
  notes: string[];
  started_fresh: boolean;
  event: {
    agent: string;
    event_type: string;
    status: string;
    output: Record<string, Json>;
    latency_ms: number;
    token_usage: TokenUsage;
  };
  transcript: Exchange[];
  scorecard?: Scorecard | null;
}

/** Research: what the agent read, scored against the task's ground truth. */
export interface RetrievalScorecard {
  kind: "retrieval";
  recall: number;
  precision: number;
  selected: string[];
  needed: string[];
  found: string[];
  missed: string[];
  helpful_hits: string[];
  irrelevant: string[];
}

/** Coder: the draft scored against the hidden acceptance test, and against the plan. */
export interface DraftScorecard {
  kind: "draft";
  acceptance_passed: boolean;
  acceptance_tests_passed: number;
  acceptance_tests_total: number;
  files_written: string[];
  planned_not_written: string[];
  written_not_planned: string[];
}

export type Scorecard = RetrievalScorecard | DraftScorecard;

export interface NewRun {
  task_id: string;
  provider?: string;
  model?: string;
  max_repair_iterations: number;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, { cache: "no-store", ...init });
  } catch {
    throw new ApiError(`Cannot reach the API at ${API_URL}. Is it running?`, 0);
  }
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {
      /* keep the status text */
    }
    throw new ApiError(detail, response.status);
  }
  return (await response.json()) as T;
}

const post = <T>(path: string, body: unknown) =>
  request<T>(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });

export const api = {
  runs: (limit = 50) => request<Run[]>(`/api/v1/runs?limit=${limit}`),
  run: (id: string) => request<Run>(`/api/v1/runs/${id}`),
  trajectory: (id: string, afterSeq = 0) =>
    request<AgentEvent[]>(`/api/v1/runs/${id}/trajectory?after_seq=${afterSeq}`),
  failures: (id: string) => request<Failure[]>(`/api/v1/runs/${id}/failures`),
  evaluation: (id: string) => request<Metric[]>(`/api/v1/runs/${id}/evaluation`),
  createRun: (body: NewRun) => post<Run>("/api/v1/runs", body),
  tasks: () => request<Task[]>("/api/v1/tasks"),
  providers: () => request<Provider[]>("/api/v1/providers"),
  stageStatus: (taskId: string) =>
    request<{ task_id: string; agents: StageRow[] }>(`/api/v1/dev/stages/${taskId}`),
  stageResult: (taskId: string, agent: string) =>
    request<StageResult>(`/api/v1/dev/stages/${taskId}/${agent}`),
  runStage: (body: { agent: string; task_id: string; provider?: string; model?: string; fresh?: boolean }) =>
    post<StageResult>("/api/v1/dev/stages", body),
};

export const TERMINAL_STATUSES = ["succeeded", "recovered", "failed", "error"];
export const isTerminal = (status: string) => TERMINAL_STATUSES.includes(status);
