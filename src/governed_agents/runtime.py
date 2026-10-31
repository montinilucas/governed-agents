"""The orchestrator: routing, permissions, tool calls and the trace.

Everything a business would need to rely on lives here, in plain code. The
model can be wrong about what the user wants; it cannot reach a tool outside
its agent, get past a role check or skip the note that a list was cut.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .brains import Brain, ToolOutcome
from .catalog import AGENTS, AGENTS_BY_ID, TOOLS_BY_NAME, Agent
from .tools import CHAT_TOP_N, ToolError, ToolResult

OUT_OF_SCOPE = {
    "pt": "Isso está fora do que os agentes da AgroDemo cobrem.",
    "en": "That is outside what the AgroDemo agents cover.",
}
DENIED = {
    "pt": "Seu perfil não tem acesso ao agente de {agent}.",
    "en": "Your role does not have access to the {agent} agent.",
}
TRUNCATED = {
    "pt": "Mostrando {shown} de {total} linhas. Quer o detalhe completo em CSV?",
    "en": "Showing {shown} of {total} rows. Want the full detail as a CSV?",
}


@dataclass
class TraceStep:
    kind: str
    detail: dict[str, Any]


@dataclass
class Turn:
    question: str
    role: str
    agent: str | None = None
    denied: bool = False
    answer: str = ""
    footer: str = ""
    trace: list[TraceStep] = field(default_factory=list)
    last_result: ToolResult | None = None

    @property
    def tools_called(self) -> list[str]:
        return [s.detail["tool"] for s in self.trace if s.kind == "tool_call"]


class Assistant:
    def __init__(
        self,
        conn: sqlite3.Connection,
        brain: Brain,
        role: str = "manager",
        lang: str = "pt",
    ) -> None:
        self.conn = conn
        self.brain = brain
        self.role = role
        self.lang = lang

    def ask(self, question: str) -> Turn:
        turn = Turn(question=question, role=self.role)
        agent_id = self.brain.route(question, AGENTS)
        turn.trace.append(TraceStep("route", {"agent": agent_id}))

        if agent_id is None or agent_id not in AGENTS_BY_ID:
            turn.answer = OUT_OF_SCOPE[self.lang]
            return turn

        agent = AGENTS_BY_ID[agent_id]
        turn.agent = agent.id
        if self.role not in agent.allowed_roles:
            turn.denied = True
            turn.trace.append(TraceStep("denied", {"agent": agent.id, "role": self.role}))
            turn.answer = DENIED[self.lang].format(agent=agent.name[self.lang])
            return turn

        turn.answer = self.brain.answer(
            question,
            agent,
            lambda name, args: self._call_tool(turn, agent, name, args),
        )
        if turn.last_result is not None and turn.last_result.truncated:
            turn.footer = TRUNCATED[self.lang].format(
                shown=len(turn.last_result.rows), total=turn.last_result.total_rows
            )
        return turn

    def _call_tool(
        self, turn: Turn, agent: Agent, name: str, args: dict[str, Any]
    ) -> ToolOutcome:
        tool = TOOLS_BY_NAME.get(name)
        if tool is None or tool.agent != agent.id:
            message = f"Tool {name!r} does not belong to the {agent.id} agent."
            turn.trace.append(TraceStep("tool_rejected", {"tool": name, "error": message}))
            return ToolOutcome(message, is_error=True)
        try:
            result = tool.run(self.conn, args, top_n=CHAT_TOP_N)
        except ToolError as error:
            turn.trace.append(TraceStep("tool_rejected", {"tool": name, "error": str(error)}))
            return ToolOutcome(str(error), is_error=True)
        turn.last_result = result
        turn.trace.append(
            TraceStep(
                "tool_call",
                {
                    "tool": name,
                    "args": result.args,
                    "rows_shown": len(result.rows),
                    "total_rows": result.total_rows,
                    "elapsed_ms": round(result.elapsed_ms, 1),
                },
            )
        )
        return ToolOutcome(result.to_model_content(), result=result)

    def export_last(self, turn: Turn, path: Path) -> int:
        """Write the full detail of the turn's last list to a CSV."""
        if turn.last_result is None:
            raise ValueError("This turn has no tool result to export.")
        tool = TOOLS_BY_NAME[turn.last_result.tool]
        return tool.export_csv(self.conn, turn.last_result.args, path)
