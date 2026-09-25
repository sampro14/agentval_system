"""LangGraph workflow (design doc §16).

START -> planner -> research -> draft -> execute -> validate
validate --pass--> evaluate -> END
validate --fail--> analyze_failure --budget left--> repair -> execute
analyze_failure --repair budget exhausted (max_repair_iterations)--> evaluate -> END
"""

from __future__ import annotations

from typing import Any, Literal

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agenteval.agents.coder.agent import draft
from agenteval.agents.executor.agent import execute
from agenteval.agents.failure_analyzer.agent import analyze_failure
from agenteval.agents.planner.agent import plan
from agenteval.agents.repairer.agent import repair
from agenteval.agents.researcher.agent import research
from agenteval.agents.validator.agent import validate
from agenteval.evaluation.report import evaluate
from agenteval.observability.events import traced
from agenteval.orchestration.deps import Deps
from agenteval.orchestration.policies import can_repair, last_validation_passed
from agenteval.orchestration.state import AgentEvalState

NODES = {
    "planner": plan,
    "research": research,
    "draft": draft,
    "execute": execute,
    "validate": validate,
    "analyze_failure": analyze_failure,
    "repair": repair,
    "evaluate": evaluate,
}


def build_graph(deps: Deps) -> CompiledStateGraph[Any, Any, Any, Any]:
    graph = StateGraph(AgentEvalState)
    for name, fn in NODES.items():
        graph.add_node(name, traced(name, fn, deps))  # type: ignore[call-overload]

    def after_validate(state: AgentEvalState) -> Literal["evaluate", "analyze_failure"]:
        return "evaluate" if last_validation_passed(state) else "analyze_failure"

    def after_analysis(state: AgentEvalState) -> Literal["repair", "evaluate"]:
        return "repair" if can_repair(state, deps.settings.max_repair_iterations) else "evaluate"

    graph.add_edge(START, "planner")
    graph.add_edge("planner", "research")
    graph.add_edge("research", "draft")
    graph.add_edge("draft", "execute")
    graph.add_edge("execute", "validate")
    graph.add_conditional_edges("validate", after_validate)
    graph.add_conditional_edges("analyze_failure", after_analysis)
    graph.add_edge("repair", "execute")
    graph.add_edge("evaluate", END)
    return graph.compile()
