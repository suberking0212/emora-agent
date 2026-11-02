from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from backend.contracts import AgentDecision


class EmoraGraphState(TypedDict):
    original_input: str
    clarification_question: str | None
    clarification_answer: str | None
    clarification_round: Literal[0, 1]
    deep_thinking: bool
    reasoning_effort: str
    analysis_summary: str | None
    decision: dict | None


class EmoraAgent:
    def __init__(self, provider):
        self.provider = provider

    async def decide(self, context: dict, *, deep_thinking: bool = False,
                     reasoning_effort: str = "medium") -> dict:
        return await self.provider.complete(context, AgentDecision, deep_thinking=deep_thinking,
                                            reasoning_effort=reasoning_effort)


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
            "analysis_summary": state["analysis_summary"],
        }, deep_thinking=state["deep_thinking"], reasoning_effort=state["reasoning_effort"])
        return {"decision": decision}

    async def ainvoke(
        self,
        *,
        original_input: str,
        clarification_question: str | None = None,
        clarification_answer: str | None = None,
        clarification_round: Literal[0, 1] = 0,
        deep_thinking: bool = False,
        reasoning_effort: str = "medium",
        analysis_summary: str | None = None,
    ) -> dict:
        state = await self.graph.ainvoke({
            "original_input": original_input,
            "clarification_question": clarification_question,
            "clarification_answer": clarification_answer,
            "clarification_round": clarification_round,
            "deep_thinking": deep_thinking,
            "reasoning_effort": reasoning_effort,
            "analysis_summary": analysis_summary,
            "decision": None,
        })
        return state["decision"]
