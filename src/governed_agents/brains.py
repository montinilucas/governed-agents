"""The two things that can drive an agent.

``RuleBrain`` is a deterministic stand-in: it lets the tests, the CI and the
evals run without an API key, and it makes the runtime's guarantees easy to
see. ``ClaudeBrain`` is the real model behind the same interface.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from .catalog import Agent
from .data import BRANCHES, PART_GROUPS
from .tools import CURRENT_FISCAL_YEAR, Tool, ToolResult

MAX_MODEL_TURNS = 6
DEFAULT_MODEL = "claude-opus-5-5"

MONTHS = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6,
    "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
}


@dataclass(frozen=True)
class ToolOutcome:
    """What the runtime hands back after a tool call, good or bad."""

    content: str
    is_error: bool = False
    result: ToolResult | None = None


CallTool = Callable[[str, dict[str, Any]], ToolOutcome]


class Brain(Protocol):
    def route(self, question: str, agents: Sequence[Agent]) -> str | None:
        """Return the id of the agent that owns the question, or None if none does."""

    def answer(self, question: str, agent: Agent, call_tool: CallTool) -> str:
        """Answer using only the agent's tools."""


def normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", normalize(text)))


def extract_period(question: str) -> str:
    text = normalize(question)
    if match := re.search(r"\bfy\s?(\d{4})\b", text):
        return f"FY{match[1]}"
    if match := re.search(r"\b(\d{4})-(0[1-9]|1[0-2])\b", text):
        return match[0]
    months = "|".join(MONTHS)
    if match := re.search(rf"\b({months})\s+(?:de\s+)?(\d{{4}})\b", text):
        return f"{match[2]}-{MONTHS[match[1]]:02d}"
    if match := re.search(r"\b(?:em|de)\s+(20\d{2})\b", text):
        return match[1]
    return CURRENT_FISCAL_YEAR


def extract_args(question: str, tool: Tool) -> dict[str, Any]:
    text = normalize(question)
    words = _words(question)
    args: dict[str, Any] = {}
    for param in tool.params:
        if param.name == "period":
            args["period"] = extract_period(question)
        elif param.name == "branch":
            args["branch"] = next((b for b in BRANCHES if normalize(b) in words), None)
        elif param.name == "part_group":
            args["part_group"] = next((g for g in PART_GROUPS if normalize(g) in words), None)
        elif param.name == "min_age_days":
            match = re.search(r"(\d+)\s*dias", text)
            args["min_age_days"] = int(match[1]) if match else None
    return args


class RuleBrain:
    """Keyword routing and template answers. No model, no network, no cost."""

    def __init__(self, lang: str = "pt") -> None:
        self.lang = lang

    def route(self, question: str, agents: Sequence[Agent]) -> str | None:
        words = _words(question)
        scored = [(len(words & set(a.keywords)), a.id) for a in agents]
        score, agent_id = max(scored, key=lambda pair: pair[0])
        return agent_id if score > 0 else None

    def answer(self, question: str, agent: Agent, call_tool: CallTool) -> str:
        text = normalize(question)

        def score(tool: Tool) -> int:
            return sum(trigger in text for trigger in tool.triggers)

        tool = max(agent.tools, key=score)
        outcome = call_tool(tool.name, extract_args(question, tool))
        if outcome.is_error or outcome.result is None:
            return outcome.content
        if not outcome.result.rows:
            return "Sem dados para esse filtro." if self.lang == "pt" else "No data for that filter."
        return tool.headline(outcome.result, self.lang)


class ModelRefused(RuntimeError):
    """The model declined the request, including after any fallback."""


ROUTER_SYSTEM = (
    "Você roteia perguntas internas da AgroDemo Máquinas, uma concessionária fictícia. "
    "Escolha o agente dono do assunto. Use \"none\" quando a pergunta não for sobre peças, "
    "serviços, máquinas ou contas a receber."
)


class ClaudeBrain:
    """Claude behind the Brain interface, with a loop this project owns.

    The loop is hand-written so the runtime can trace each call, cap the turns
    and return tool errors for the model to correct.
    """

    def __init__(self, client: Any = None, model: str = DEFAULT_MODEL) -> None:
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self._client = client
        self._model = model

    def _create(self, **params: Any) -> Any:
        response = self._client.beta.messages.create(
            model=self._model,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            **params,
        )
        if response.stop_reason == "refusal":
            raise ModelRefused(getattr(response.stop_details, "category", None) or "refusal")
        return response

    @staticmethod
    def _text(response: Any) -> str:
        return "\n".join(b.text for b in response.content if b.type == "text").strip()

    def route(self, question: str, agents: Sequence[Agent]) -> str | None:
        options = [agent.id for agent in agents] + ["none"]
        catalog = "\n".join(f"- {a.id}: {a.purpose}" for a in agents)
        response = self._create(
            max_tokens=1024,
            system=f"{ROUTER_SYSTEM}\n\nAgentes:\n{catalog}",
            messages=[{"role": "user", "content": question}],
            output_config={
                "effort": "low",
                "format": {
                    "type": "json_schema",
                    "schema": {
                        "type": "object",
                        "properties": {"agent": {"type": "string", "enum": options}},
                        "required": ["agent"],
                        "additionalProperties": False,
                    },
                },
            },
        )
        agent_id = json.loads(self._text(response))["agent"]
        return None if agent_id == "none" else agent_id

    def answer(self, question: str, agent: Agent, call_tool: CallTool) -> str:
        tools = [tool.to_anthropic() for tool in agent.tools]
        messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
        for _ in range(MAX_MODEL_TURNS):
            response = self._create(
                max_tokens=16000,
                system=agent.system_prompt,
                tools=tools,
                messages=messages,
                output_config={"effort": "medium"},
            )
            if response.stop_reason != "tool_use":
                return self._text(response)
            messages.append({"role": "assistant", "content": response.content})
            results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                outcome = call_tool(block.name, block.input)
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": outcome.content,
                        "is_error": outcome.is_error,
                    }
                )
            messages.append({"role": "user", "content": results})
        raise RuntimeError(f"No final answer after {MAX_MODEL_TURNS} model turns.")
