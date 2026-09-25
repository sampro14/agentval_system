# AgentEval --- Multi-Agent Reliability & Evaluation Platform

## 1. Document Status

  -----------------------------------------------------------------------
  Field                               Value
  ----------------------------------- -----------------------------------
  Project                             AgentEval

  Document Type                       System Design

  Version                             1.0

  Status                              Proposed

  Primary Goal                        Evaluate, observe, diagnose, and
                                      improve reliability of multi-agent
                                      AI systems

  Initial Workload                    Autonomous software-engineering
                                      workflow

  Primary Orchestrator                LangGraph

  API Layer                           FastAPI

  Execution                           Docker sandbox + Playwright /
                                      Pytest

  Persistence                         PostgreSQL

  Queue / Runtime State               Redis

  UI                                  React / Next.js

  Observability                       OpenTelemetry
  -----------------------------------------------------------------------

------------------------------------------------------------------------

## 2. Executive Summary

AgentEval is a production-oriented platform for evaluating the
reliability of multi-agent AI systems.

Instead of measuring only whether an AI system eventually succeeds,
AgentEval captures and evaluates the complete execution trajectory:

**Plan → Research → Draft → Execute → Validate → Diagnose → Repair →
Re-execute → Final Verification**

The platform provides:

-   Multi-agent workflow orchestration
-   Tool and execution tracing
-   Stage-level evaluation
-   Failure detection and root-cause attribution
-   Autonomous repair and recovery
-   Cost and latency measurement
-   Evidence-based validation
-   Agent trajectory visualization
-   Benchmarking and experiment management

The initial reference workload is an autonomous software-engineering
task, but the evaluation framework should remain workload-agnostic so
that it can later evaluate research agents, RAG agents, SQL agents,
browser agents, and enterprise automation agents.

------------------------------------------------------------------------

# 3. Problem Statement

Modern LLM agents can perform increasingly complex tasks, but production
reliability remains difficult to measure.

A simple metric such as:

> Task Success Rate = 82%

does not explain why the remaining 18% failed.

A multi-agent workflow may fail because of:

-   Poor task decomposition
-   Missing context
-   Incorrect retrieval
-   Wrong tool selection
-   Invalid tool parameters
-   Incorrect code generation
-   Environment failures
-   Weak validation
-   Incomplete requirement coverage
-   Faulty repair
-   Excessive retries
-   Hallucinated assumptions

AgentEval addresses this gap by treating an agent execution as an
observable trajectory rather than a black box.

------------------------------------------------------------------------

# 4. Goals

## 4.1 Primary Goals

1.  Execute complex multi-agent workflows in an isolated environment.
2.  Capture every important agent decision, tool call, observation, and
    outcome.
3.  Evaluate each stage independently.
4.  Attribute failures to likely root causes.
5.  Support automated recovery and repair.
6.  Measure reliability, cost, latency, and efficiency.
7.  Provide a UI for inspecting complete agent trajectories.
8.  Enable repeatable benchmarks and experiments.
9.  Support comparison between agent versions, prompts, models, and
    workflows.
10. Provide research-quality evaluation data.

## 4.2 Secondary Goals

-   Support multiple LLM providers.
-   Support different agent architectures.
-   Support human review.
-   Export experiment results.
-   Provide APIs for external agents to submit evaluation runs.

------------------------------------------------------------------------

# 5. Non-Goals

The first version will not attempt to:

-   Train foundation models.
-   Replace a full observability platform.
-   Guarantee formal correctness of arbitrary AI outputs.
-   Automatically classify every possible failure with perfect accuracy.
-   Provide unrestricted production access to external systems.
-   Support arbitrary autonomous financial, medical, or high-risk
    actions.

------------------------------------------------------------------------

# 6. Initial Use Case

## Autonomous Software Engineering

Example request:

> "Add a password reset feature to the application. Users should be able
> to request a reset, receive a verification token, set a new password,
> and have automated tests covering the complete flow."

AgentEval orchestrates:

``` text
User Request
     |
     v
Planner
     |
     v
Research / Context Agent
     |
     v
Coding / Drafting Agent
     |
     v
Execution Agent
     |
     v
Validator
     |
     +-------- PASS --------> Final Verification
     |
     +-------- FAIL --------> Failure Analyzer
                                   |
                                   v
                              Repair Agent
                                   |
                                   v
                                Execute
                                   |
                                   +----> Validate
```

------------------------------------------------------------------------

# 7. High-Level Architecture

``` text
                         +----------------------+
                         |        User          |
                         +----------+-----------+
                                    |
                                    v
                         +----------------------+
                         |      FastAPI API     |
                         +----------+-----------+
                                    |
                                    v
                         +----------------------+
                         |   LangGraph Runtime  |
                         +----------+-----------+
                                    |
             +----------------------+----------------------+
             |                      |                      |
             v                      v                      v
      +-------------+        +-------------+        +-------------+
      |   Planner   |        |  Research   |        |   Drafter   |
      +------+------+        +------+------+        +------+------+
             |                      |                      |
             +----------------------+----------------------+
                                    |
                                    v
                         +----------------------+
                         |      Executor        |
                         +----------+-----------+
                                    |
                      +-------------+-------------+
                      |                           |
                      v                           v
                +-----------+               +-----------+
                | Playwright|               |  Pytest   |
                +-----+-----+               +-----+-----+
                      |                           |
                      +-------------+-------------+
                                    |
                                    v
                         +----------------------+
                         |      Validator       |
                         +----------+-----------+
                                    |
                          +---------+---------+
                          |                   |
                         PASS                FAIL
                          |                   |
                          |                   v
                          |          +------------------+
                          |          | Failure Analyzer |
                          |          +--------+---------+
                          |                   |
                          |                   v
                          |          +------------------+
                          |          |  Repair Agent    |
                          |          +--------+---------+
                          |                   |
                          +-------------------+
                                    |
                                    v
                         +----------------------+
                         |     AgentEval        |
                         | Evaluation Engine    |
                         +----------+-----------+
                                    |
              +---------------------+---------------------+
              |                     |                     |
              v                     v                     v
         Reliability            Trajectory            Cost/Latency
           Metrics               Analysis               Metrics
              |                     |                     |
              +---------------------+---------------------+
                                    |
                                    v
                         +----------------------+
                         | PostgreSQL + Redis   |
                         +----------+-----------+
                                    |
                                    v
                         +----------------------+
                         | React / Next.js UI   |
                         +----------------------+
```

------------------------------------------------------------------------

# 8. Core Agents

## 8.1 Planner Agent

### Responsibility

Convert a natural-language task into an executable plan.

### Input

-   User request
-   Repository metadata
-   Available tools
-   System constraints

### Output

Structured task graph containing:

-   Task IDs
-   Descriptions
-   Dependencies
-   Expected outputs
-   Validation criteria

### Example

``` json
{
  "goal": "Implement password reset",
  "tasks": [
    {
      "id": "T1",
      "description": "Inspect authentication architecture",
      "dependencies": []
    },
    {
      "id": "T2",
      "description": "Implement reset-token generation",
      "dependencies": ["T1"]
    },
    {
      "id": "T3",
      "description": "Implement password reset API",
      "dependencies": ["T2"]
    },
    {
      "id": "T4",
      "description": "Implement frontend flow",
      "dependencies": ["T3"]
    },
    {
      "id": "T5",
      "description": "Create integration tests",
      "dependencies": ["T3", "T4"]
    }
  ]
}
```

------------------------------------------------------------------------

## 8.2 Research / Context Agent

### Responsibility

Gather relevant information before execution.

### Sources

-   Repository files
-   Documentation
-   Existing tests
-   Database schema
-   API specifications
-   Configuration
-   Internal knowledge base

### Evaluation

-   Context relevance
-   Evidence completeness
-   Retrieval recall
-   Irrelevant context rate

------------------------------------------------------------------------

## 8.3 Drafting / Coding Agent

### Responsibility

Produce the requested implementation.

### Controls

The agent operates inside an isolated workspace and all modifications
are captured as diffs.

### Captured Data

-   Files modified
-   Lines changed
-   Tools used
-   Commands executed
-   Generated artifacts
-   Agent reasoning metadata where available and appropriate

------------------------------------------------------------------------

## 8.4 Execution Agent

### Responsibility

Run generated artifacts against controlled environments.

### Supported execution modes

-   Playwright browser tests
-   Pytest
-   API tests
-   Static analysis
-   Linting
-   Compilation
-   SQL validation

The execution layer is treated as an evidence source rather than
trusting the agent's claim that a task succeeded.

------------------------------------------------------------------------

## 8.5 Validator Agent

### Responsibility

Determine whether the result satisfies the original requirements.

### Validation dimensions

1.  Functional correctness
2.  Requirement coverage
3.  Regression safety
4.  Test coverage
5.  Output correctness
6.  Security checks
7.  Quality constraints

### Inputs

``` text
Original Requirement
+
Agent Plan
+
Code / Artifacts
+
Execution Results
+
Test Results
+
Evidence
```

------------------------------------------------------------------------

## 8.6 Failure Analyzer

### Responsibility

Identify why a workflow failed.

### Failure categories

``` text
PLANNING
RETRIEVAL
CONTEXT
TOOL_SELECTION
TOOL_EXECUTION
CODE_GENERATION
VALIDATION
ENVIRONMENT
REPAIR
UNKNOWN
```

### Output

``` json
{
  "failure_type": "CODE_GENERATION",
  "root_cause": "Database migration was not created",
  "confidence": 0.91,
  "evidence": [
    "Integration test failed with missing table",
    "Git diff contains model but no migration"
  ],
  "recommended_action": "Create database migration and rerun integration tests"
}
```

------------------------------------------------------------------------

## 8.7 Repair Agent

### Responsibility

Apply a targeted correction based on failure evidence.

### Repair loop

``` text
Failure
   |
   v
Root Cause
   |
   v
Repair Proposal
   |
   v
Apply Repair
   |
   v
Execute
   |
   v
Validate
   |
   +---- PASS
   |
   +---- FAIL --> Failure Analyzer
```

### Safety

A configurable maximum repair count prevents infinite loops.

Initial default:

``` text
MAX_REPAIR_ITERATIONS = 3
```

------------------------------------------------------------------------

# 9. AgentEval Evaluation Engine

The evaluation engine is the core research component.

It evaluates the workflow at multiple levels.

## 9.1 Planning Metrics

-   Task decomposition accuracy
-   Dependency accuracy
-   Missing task rate
-   Unnecessary task rate
-   Plan-to-execution alignment

## 9.2 Retrieval Metrics

-   Context relevance
-   Evidence recall
-   Citation correctness
-   Missing evidence rate
-   Irrelevant retrieval rate

## 9.3 Tool Metrics

-   Tool selection accuracy
-   Parameter correctness
-   Tool failure rate
-   Unnecessary tool calls
-   Tool retry count

## 9.4 Execution Metrics

-   Task success
-   Test pass rate
-   Runtime failures
-   Regression failures
-   Execution latency

## 9.5 Validation Metrics

-   Requirement coverage
-   Defect detection rate
-   False positive rate
-   False negative rate
-   Evidence quality

## 9.6 Recovery Metrics

-   Repair success rate
-   Average repair iterations
-   Recovery time
-   Regression after repair

## 9.7 Resource Metrics

-   Input tokens
-   Output tokens
-   Total tokens
-   LLM calls
-   Tool calls
-   Execution time
-   Estimated cost

------------------------------------------------------------------------

# 10. Reliability Score

AgentEval should avoid representing reliability as one opaque number.

A multidimensional scorecard should be the primary representation.

Example:

``` text
Agent Reliability Report

Planning             91%
Context / Retrieval  76%
Tool Selection       84%
Execution            87%
Validation           72%
Recovery             89%

Task Success         84%
```

An optional aggregate score can be computed only when the weighting
methodology is explicitly configured.

The raw component metrics must always remain visible.

------------------------------------------------------------------------

# 11. Failure Attribution

The primary research feature is trajectory-level failure attribution.

## Example

A workflow fails.

Traditional monitoring reports:

``` text
TASK FAILED
```

AgentEval reports:

``` text
Task: Password Reset
Status: Recovered

Initial Failure:
Integration Test Failure

Root Cause:
Coding Agent

Category:
CODE_GENERATION

Evidence:
- Password reset model created
- Database migration absent
- Test failed because table was unavailable

Repair:
Migration generated

Repair Result:
All tests passed

Repair Iterations:
1
```

This makes the system useful for improving agent architectures.

------------------------------------------------------------------------

# 12. Trajectory Model

Every run should produce a structured trajectory.

``` text
Run
 |
 +-- Task
 |
 +-- Plan
 |
 +-- Agent Events
 |    |
 |    +-- Agent Input
 |    +-- Agent Output
 |    +-- Tool Calls
 |    +-- Tool Results
 |    +-- Decisions
 |
 +-- Execution Events
 |
 +-- Validation Events
 |
 +-- Failure Events
 |
 +-- Repair Events
 |
 +-- Final Result
 |
 +-- Evaluation
```

------------------------------------------------------------------------

# 13. Event Schema

Example event:

``` json
{
  "run_id": "run_10291",
  "event_id": "evt_8291",
  "timestamp": "2026-09-25T12:00:00Z",
  "agent": "validator",
  "event_type": "VALIDATION_FAILED",
  "input_ref": "artifact_123",
  "output": {
    "status": "failed",
    "reason": "Missing database migration"
  },
  "latency_ms": 1230,
  "token_usage": {
    "input": 1200,
    "output": 450
  }
}
```

------------------------------------------------------------------------

# 14. Data Model

## Run

``` text
runs
----
id
task
workflow_version
model_config
status
started_at
completed_at
total_tokens
total_cost
total_latency
final_score
```

## Agent Event

``` text
agent_events
------------
id
run_id
agent_name
event_type
input
output
status
latency
token_usage
created_at
```

## Tool Call

``` text
tool_calls
----------
id
run_id
agent_event_id
tool_name
arguments
result
status
latency
```

## Evaluation

``` text
evaluations
-----------
id
run_id
metric
score
evidence
evaluator
created_at
```

## Failure

``` text
failures
--------
id
run_id
stage
category
root_cause
confidence
evidence
resolved
```

## Repair

``` text
repairs
-------
id
run_id
failure_id
iteration
action
result
status
```

------------------------------------------------------------------------

# 15. API Design

## Create Run

``` http
POST /api/v1/runs
```

Request:

``` json
{
  "task": "Implement password reset",
  "workflow": "software_engineering",
  "model": "configured-model",
  "max_repair_iterations": 3
}
```

------------------------------------------------------------------------

## Get Run

``` http
GET /api/v1/runs/{run_id}
```

------------------------------------------------------------------------

## Get Trajectory

``` http
GET /api/v1/runs/{run_id}/trajectory
```

------------------------------------------------------------------------

## Get Evaluation

``` http
GET /api/v1/runs/{run_id}/evaluation
```

------------------------------------------------------------------------

## Get Failures

``` http
GET /api/v1/runs/{run_id}/failures
```

------------------------------------------------------------------------

## Compare Runs

``` http
POST /api/v1/evaluations/compare
```

Example use:

``` text
Model A vs Model B
Prompt v1 vs Prompt v2
Agent architecture v1 vs v2
RAG vs Agentic RAG
```

------------------------------------------------------------------------

# 16. LangGraph State

A simplified workflow state:

``` python
class AgentEvalState(TypedDict):
    run_id: str
    task: str
    plan: dict
    context: list
    artifacts: list
    execution_results: list
    validation_results: list
    failures: list
    repairs: list
    trajectory: list
    metrics: dict
    status: str
```

Graph:

``` text
START
  |
Planner
  |
Research
  |
Draft
  |
Execute
  |
Validate
  |
  +---- success ----> Evaluate
  |
  +---- failure ----> Analyze Failure
                         |
                         v
                       Repair
                         |
                         v
                       Execute
                         |
                         v
                      Validate
                         |
                         +---- success --> Evaluate
                         |
                         +---- failure --> retry / terminate
```

------------------------------------------------------------------------

# 17. Isolation and Security

Agent execution must not occur directly on the host machine.

Use:

``` text
Agent
  |
  v
Execution Manager
  |
  v
Ephemeral Docker Container
  |
  +-- Repository
  +-- Dependencies
  +-- Test Runner
  +-- Browser
```

Controls:

-   CPU limit
-   Memory limit
-   Execution timeout
-   Network policy
-   Filesystem isolation
-   Process limits
-   Workspace cleanup
-   Tool allowlist

------------------------------------------------------------------------

# 18. Observability

Use OpenTelemetry-compatible tracing.

Trace hierarchy:

``` text
Run
 |
 +-- Planner span
 |
 +-- Research span
 |     |
 |     +-- Retrieval span
 |     +-- Search span
 |
 +-- Coding span
 |     |
 |     +-- LLM call
 |     +-- File modification
 |
 +-- Execution span
 |     |
 |     +-- Playwright
 |     +-- Pytest
 |
 +-- Validation span
 |
 +-- Repair span
```

This allows correlation of:

-   Latency
-   Token usage
-   Tool calls
-   Failures
-   Agent decisions
-   Final outcome

------------------------------------------------------------------------

# 19. UI Design

## Dashboard

``` text
AgentEval
────────────────────────────────────────────

Total Runs                  1,284
Success Rate                 84.2%
Recovery Rate                71.8%
Avg Latency                  4.7 sec
Avg Tokens                   3,821

Failure Attribution

Retrieval          24%
Planning           21%
Tool Selection     18%
Coding             16%
Validation         12%
Environment         6%
Other               3%
```

## Run Details

``` text
Run #10291

Task:
Implement password reset

✓ Planner
✓ Research
✓ Coding
✗ Validation
✓ Failure Analysis
✓ Repair
✓ Final Validation

Status: RECOVERED
Repair Iterations: 1
```

## Trajectory Viewer

Users should be able to expand each event and inspect:

-   Input
-   Output
-   Tool calls
-   Tool result
-   Execution evidence
-   Evaluation
-   Failure classification
-   Latency
-   Token usage

------------------------------------------------------------------------

# 20. Benchmarking Framework

AgentEval should support repeatable experiments.

Example benchmark:

``` text
Benchmark: SWE-Auth-001

Tasks: 100

Configurations:
A. Baseline single agent
B. Planner + coder
C. Planner + coder + validator
D. Full AgentEval workflow
```

Metrics:

``` text
Task Success
First-pass Success
Recovery Success
Failure Attribution Accuracy
Average Tokens
Average Latency
Repair Iterations
Regression Rate
```

------------------------------------------------------------------------

# 21. Research Methodology

## Research Question

> Can trajectory-level failure attribution identify the root causes of
> multi-agent system failures more accurately than end-to-end success
> metrics alone?

## Baseline 1

End-to-end task success only.

## Baseline 2

LLM-as-a-Judge evaluation.

## Proposed System

Trajectory-based evaluation using:

-   Agent events
-   Tool traces
-   Execution evidence
-   Validation results
-   Failure classification
-   Repair outcomes

------------------------------------------------------------------------

# 22. Experiments

## Experiment 1 --- Reliability

Compare:

``` text
Single Agent
vs
Multi-Agent
vs
Multi-Agent + Validation
vs
Multi-Agent + Validation + Repair
```

Measure task success and recovery.

## Experiment 2 --- Failure Attribution

Inject controlled failures:

-   Wrong tool
-   Missing context
-   Incorrect plan
-   Broken code
-   Invalid parameter
-   Test environment failure

Measure root-cause classification accuracy.

## Experiment 3 --- Repair

Measure:

-   Initial failure rate
-   Successful recovery
-   Number of iterations
-   Regression after repair

## Experiment 4 --- Cost

Compare:

-   Tokens
-   Tool calls
-   Latency
-   Cost per successful task

## Experiment 5 --- Ablation

Remove one component at a time:

``` text
Full System
- Failure Analyzer
- Execution Evidence
- Trajectory Data
- Validator
- Repair
```

Measure the effect on reliability and diagnosis quality.

------------------------------------------------------------------------

# 23. Success Criteria

The MVP is successful when it can:

1.  Execute at least one realistic multi-agent workflow.
2.  Capture complete execution trajectories.
3.  Run generated artifacts in an isolated environment.
4.  Detect validation failures.
5.  Classify failures into defined categories.
6.  Perform at least one automated repair cycle.
7.  Produce stage-level evaluation metrics.
8.  Display an end-to-end trajectory in the UI.
9.  Compare multiple agent configurations.
10. Export benchmark results.

------------------------------------------------------------------------

# 24. MVP Scope

## Phase 1 --- Core Runtime

-   FastAPI
-   LangGraph
-   PostgreSQL
-   Redis
-   Basic Planner
-   Coding Agent
-   Executor

## Phase 2 --- Validation

-   Pytest
-   Playwright
-   Validator Agent
-   Failure Analyzer

## Phase 3 --- Evaluation

-   Trajectory storage
-   Metrics engine
-   Token tracking
-   Latency tracking
-   Reliability report

## Phase 4 --- Recovery

-   Repair Agent
-   Retry policy
-   Recovery metrics

## Phase 5 --- UI

-   Dashboard
-   Run viewer
-   Trajectory viewer
-   Failure analysis
-   Benchmark comparison

## Phase 6 --- Research

-   Benchmark dataset
-   Baselines
-   Ablation experiments
-   Failure taxonomy
-   Research report

------------------------------------------------------------------------

# 25. Repository Structure

``` text
agenteval/
│
├── apps/
│   ├── api/
│   └── web/
│
├── agents/
│   ├── planner/
│   ├── researcher/
│   ├── coder/
│   ├── executor/
│   ├── validator/
│   ├── failure_analyzer/
│   └── repairer/
│
├── orchestration/
│   ├── graph.py
│   ├── state.py
│   └── policies.py
│
├── evaluation/
│   ├── metrics/
│   ├── evaluators/
│   ├── failure_attribution/
│   └── benchmarks/
│
├── execution/
│   ├── docker/
│   ├── playwright/
│   └── pytest/
│
├── observability/
│   ├── tracing/
│   ├── logging/
│   └── telemetry/
│
├── storage/
│   ├── models/
│   ├── repositories/
│   └── migrations/
│
├── datasets/
├── experiments/
├── tests/
├── docs/
│
├── docker-compose.yml
├── pyproject.toml
└── README.md
```

------------------------------------------------------------------------

# 26. Technology Stack

  Layer                 Technology
  --------------------- ---------------------------------
  Agent orchestration   LangGraph
  Backend               FastAPI
  Language              Python
  LLM                   Provider abstraction
  Database              PostgreSQL
  Queue / state         Redis
  Browser automation    Playwright
  Testing               Pytest
  Isolation             Docker
  Frontend              React / Next.js
  Tracing               OpenTelemetry
  Embeddings            Configurable embedding provider
  Deployment            Docker / Kubernetes-ready

------------------------------------------------------------------------

# 27. Key Design Principles

## Evidence over claims

Never assume an agent succeeded because it said it succeeded.

## Observable by default

Every important action should generate an event.

## Evaluation is multi-dimensional

Do not reduce agent quality to one score.

## Recovery is measurable

A successful repair is different from first-pass success.

## Workload agnostic

The evaluation engine should eventually support multiple agent types.

## Reproducible experiments

Prompts, model configuration, workflow version and evaluation
configuration must be versioned.

## Safe execution

Generated code and tool actions must execute inside controlled
environments.

------------------------------------------------------------------------

# 28. Future Extensions

Potential future research directions:

### Adaptive Agent Routing

Automatically choose which agent should handle each task.

### Learned Failure Attribution

Train a classifier using historical trajectories.

### Self-Improving Agents

Use evaluation results to automatically improve prompts, tools, routing
policies, or planning strategies.

### Cross-Agent Benchmarking

Compare different agent frameworks and models under identical workloads.

### Agent Memory Evaluation

Measure whether long-term memory improves or harms task reliability.

### RAG Evaluation

Add retrieval-specific metrics and evidence-grounded evaluation.

### Multi-Agent Security

Evaluate prompt injection, tool misuse, memory poisoning, and privilege
escalation.

### Human-in-the-Loop Evaluation

Allow human reviewers to validate or override automated evaluations.

------------------------------------------------------------------------

# 29. Portfolio / CV Positioning

The project should be presented as:

> **AgentEval --- Multi-Agent Reliability & Evaluation Platform**

### Short description

> An open-source platform for evaluating production AI agents through
> trajectory-level observability, stage-wise evaluation, failure
> attribution, autonomous recovery, and cost/latency analysis.

### CV bullet

> Designed and implemented a multi-agent reliability platform
> orchestrating planning, research, code generation, execution,
> validation and autonomous repair, with trajectory-level failure
> attribution and stage-wise evaluation.

### Research bullet

> Investigated trajectory-based failure attribution for diagnosing
> reliability bottlenecks in LLM agents, comparing end-to-end success
> metrics, LLM-as-a-Judge and execution-evidence-based evaluation.

------------------------------------------------------------------------

# 30. Final Architecture

The complete vision is:

``` text
                         USER
                           |
                           v
                    +-------------+
                    |   PLANNER   |
                    +------+------+
                           |
                           v
                    +-------------+
                    |  RESEARCH   |
                    +------+------+
                           |
                           v
                    +-------------+
                    |   DRAFTER   |
                    +------+------+
                           |
                           v
                    +-------------+
                    |  EXECUTOR   |
                    +------+------+
                           |
                           v
                    +-------------+
                    |  VALIDATOR  |
                    +------+------+
                           |
                    +------+------+
                    |             |
                   PASS          FAIL
                    |             |
                    |             v
                    |      +-------------+
                    |      |   FAILURE   |
                    |      |   ANALYZER  |
                    |      +------+------+
                    |             |
                    |             v
                    |      +-------------+
                    |      |   REPAIR    |
                    |      +------+------+
                    |             |
                    |             +-------> EXECUTOR
                    |
                    v
             +-------------+
             |  FINAL EVAL |
             +------+------+
                    |
                    v
        +-------------------------+
        |       AgentEval         |
        |-------------------------|
        | Planning                |
        | Retrieval               |
        | Tool Use                |
        | Execution               |
        | Validation              |
        | Recovery                |
        | Cost                    |
        | Latency                 |
        | Failure Attribution     |
        +------------+------------+
                     |
                     v
              RELIABILITY REPORT
```

## Core thesis

**AgentEval should not be another agent framework.**

Its purpose is to answer a harder production question:

> **"Can we trust this AI agent to perform complex tasks, and when it
> fails, can we explain exactly what went wrong, recover from it, and
> measure whether the system is actually improving?"**

That is the central design principle around which the entire platform
should be built.
