# Conversation Generation Retest

Date: 2026-11-02  
Scope: current `emora-mvp-standalone/` checkout and one live comparison using its configured Provider.  
Persistence: the live calls used `database=None`; no application database was read or written. The free-form response was inspected in memory and was not saved as a transcript.

## Findings

### 1. Application constraints are a direct cause of the fixed report form

The current model contract only offers `ask_clarification` or `complete`. A completed answer must contain `summary`, `emotions`, `conflict`, `user_needs`, at least one `suggestion`, and `next_step`; the system prompt explicitly asks for those fields. `AnalysisService` then converts those fields into headings while streaming, and `frontend/static/app.js` repeats the same headings when restoring or rendering a completed response.

The model writes the content inside each field, but it cannot choose a conversational response without those sections, decline to advise, or decide that a short acknowledgment is enough. In the supplied screen output, headings such as “情境概括” and “可以尝试” are application-rendered labels. “运行记录”, the stage names, and the empty tool/skill message are frontend status UI, not model-generated conversation.

The input/output Guard gates and persistence stages are separate control boundaries. They do not create the report headings.

### 2. There is no evidence that LangGraph causes the fixed form

The current graph is one `agent` node with `START -> agent -> END`. The node forwards the request context to `ProviderAdapter.complete()` and returns its decision. It does not add headings, require suggestions, or change the rendered response. Those constraints live in the prompt, Pydantic contract, service formatter, and frontend renderer.

The graph therefore contributes little orchestration in this one-shot model call, but replacing LangGraph alone would leave the same contract and renderer in place. The live comparison changed both the graph and the response contract, so it is not a controlled experiment on LangGraph by itself; this conclusion comes from the current source path.

The previous audit's claim that completed turns were not sent as context is stale for the current checkout. The current service stores completed turns per session, bounds the history by configured limits, and passes recent turns to the model. This improves continuity but does not change the fixed completion format.

### 3. Native conversational output is demonstrated once; reasoning quality is not established

One live A/B comparison used the supplied employment-distress input and the configured `gpt-5.6-terra` endpoint, requesting `reasoning_effort=medium` in both calls:

- Current service path: input Guard, one-node LangGraph, `AgentDecision` JSON contract, and output Guard; no database.
- Free-form path: same Provider and input, direct Chat Completions call with no JSON response format or application schema, followed by the output Guard; no graph or database.

The structured path completed with the required report fields and five suggestions. The direct path returned Chinese prose with an empathic opening, discussed the economic context, then asked for job-search details and offered to help. Its output Guard result was `Safe` and the Provider finished normally. This is evidence that the endpoint can produce a conversational form when the application contract is removed.

The free-form answer still gave substantial unsolicited advice, inferred a severe “人生没有出口” framing that the input did not state, and added crisis guidance without an explicit self-harm cue. Removing the schema alone is not a sufficient interaction policy or quality fix.

The request included `reasoning_effort=medium`; the provider reported 43 reasoning tokens for the free-form call. That usage field does not prove that the gateway honored the requested effort as intended, that a larger reasoning budget would improve this response, or that the model's general reasoning capability is fully exercised. The existing runtime reports this setting as accepted with its effect unverified.

## Decision

The evidence separates the three questions:

1. **Application expression limit:** confirmed. Prompt, required fields, output action set, stream formatter, and frontend renderer enforce report-shaped answers.
2. **LangGraph as the cause:** unsupported. The graph is a pass-through wrapper around one Provider call; it does not impose the output format.
3. **Native model capability:** partially demonstrated for free-form Chinese dialogue, not evaluated as a general reasoning benchmark. One sample cannot establish reliability or the effect of reasoning settings.

The next architecture experiment should change the response contract and renderer together while retaining independent Guard checks and storage. It should be evaluated over the existing intent-diverse scenarios, including “只想倾诉、不需要建议”, before deciding whether any graph routing is needed. The current comparison is diagnostic evidence, not a general quality evaluation.
