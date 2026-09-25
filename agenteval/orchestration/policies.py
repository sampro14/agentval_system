from agenteval.orchestration.state import AgentEvalState


def can_repair(state: AgentEvalState, max_repair_iterations: int) -> bool:
    """Guard against infinite repair loops (design doc §8.7)."""
    return len(state.get("repairs", [])) < max_repair_iterations


def last_validation_passed(state: AgentEvalState) -> bool:
    results = state.get("validation_results", [])
    return bool(results) and results[-1].get("passed", False)
