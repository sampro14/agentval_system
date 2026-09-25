import operator
from typing import Annotated, Any, TypedDict


class AgentEvalState(TypedDict, total=False):
    run_id: str
    task: str
    workspace: str
    plan: dict[str, Any]
    context: list[dict[str, Any]]
    artifacts: list[dict[str, Any]]
    execution_results: list[dict[str, Any]]
    validation_results: list[dict[str, Any]]
    failures: list[dict[str, Any]]
    repairs: list[dict[str, Any]]
    # Nodes return only their new events; LangGraph appends them.
    trajectory: Annotated[list[dict[str, Any]], operator.add]
    metrics: dict[str, Any]
    status: str
