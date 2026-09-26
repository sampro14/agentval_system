"""Relational data model (design doc §14)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    task: Mapped[str] = mapped_column(Text)
    workflow: Mapped[str] = mapped_column(String(64), default="software_engineering")
    workflow_version: Mapped[str] = mapped_column(String(32), default="0.1.0")
    model_config_: Mapped[dict[str, Any]] = mapped_column("model_config", default=dict)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    total_cost: Mapped[float | None] = mapped_column(Float)
    total_latency_ms: Mapped[int | None] = mapped_column(Integer)
    final_score: Mapped[float | None] = mapped_column(Float)

    @property
    def provider(self) -> str | None:
        return self.model_config_.get("llm_provider")

    @property
    def model(self) -> str | None:
        return self.model_config_.get("llm_model")

    @property
    def task_id(self) -> str | None:
        return self.model_config_.get("task_id")


class AgentEvent(Base):
    __tablename__ = "agent_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    agent_name: Mapped[str] = mapped_column(String(64))
    event_type: Mapped[str] = mapped_column(String(64))
    input: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    output: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(32))
    latency_ms: Mapped[int] = mapped_column(Integer)
    token_usage: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ToolCall(Base):
    __tablename__ = "tool_calls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    agent_event_id: Mapped[str | None] = mapped_column(ForeignKey("agent_events.id", ondelete="CASCADE"))
    tool_name: Mapped[str] = mapped_column(String(128))
    arguments: Mapped[dict[str, Any]] = mapped_column(default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32))
    latency_ms: Mapped[int | None] = mapped_column(Integer)


class Evaluation(Base):
    __tablename__ = "evaluations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    metric: Mapped[str] = mapped_column(String(128))
    score: Mapped[float] = mapped_column(Float)
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    evaluator: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Failure(Base):
    __tablename__ = "failures"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    stage: Mapped[str] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(32))
    root_cause: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float)
    evidence: Mapped[list[Any]] = mapped_column(default=list)
    recommended_action: Mapped[str | None] = mapped_column(Text)
    attribution_method: Mapped[str | None] = mapped_column(String(16))
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)


class Repair(Base):
    __tablename__ = "repairs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    failure_id: Mapped[str | None] = mapped_column(ForeignKey("failures.id", ondelete="SET NULL"))
    iteration: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(Text)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32))
