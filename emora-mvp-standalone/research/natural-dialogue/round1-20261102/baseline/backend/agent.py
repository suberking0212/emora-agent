from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from backend.contracts import AgentDecision
from backend.provider import TextStreamHandler


class EmoraGraphState(TypedDict):
    original_input: str
    clarification_question: str | None
    clarification_answer: str | None
    clarification_round: Literal[0, 1]
    reasoning_effort: str
    on_text: TextStreamHandler | None
    decision: dict | None
    history: list[dict[str, str]]


class EmoraAgent:
    def __init__(self, provider):
        self.provider = provider

    async def decide(self, context: dict, *, reasoning_effort: str = "medium",
                     on_text: TextStreamHandler | None = None) -> dict:
        return await self.provider.complete(context, AgentDecision,
                                            reasoning_effort=reasoning_effort, on_text=on_text)


class EmoraAgentGraph:
    def __init__(self, agent):
        self.agent = agent
        builder = StateGraph(EmoraGraphState)
        builder.add_node("agent", self._agent_node)
        builder.add_edge(START, "agent")
        builder.add_edge("agent", END)
        self.graph = builder.compile()

    async def _agent_node(self, state: EmoraGraphState) -> dict:
        decision = await self.agent.decide({
            "original_input": state["original_input"],
            "clarification_question": state["clarification_question"],
            "clarification_answer": state["clarification_answer"],
            "clarification_round": state["clarification_round"],
            "history": state["history"],
        }, reasoning_effort=state["reasoning_effort"],
            on_text=state["on_text"])
        return {"decision": decision}

    async def ainvoke(
        self,
        *,
        original_input: str,
        clarification_question: str | None = None,
        clarification_answer: str | None = None,
        clarification_round: Literal[0, 1] = 0,
        reasoning_effort: str = "medium",
        on_text: TextStreamHandler | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> dict:
        state = await self.graph.ainvoke({
            "original_input": original_input,
            "clarification_question": clarification_question,
            "clarification_answer": clarification_answer,
            "clarification_round": clarification_round,
            "reasoning_effort": reasoning_effort,
            "on_text": on_text,
            "decision": None,
            "history": history or [],
        })
        return state["decision"]
