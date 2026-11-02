# Natural conversation contract (isolated candidate)

This candidate is not deployed or approved for production merge. The review and
validation artifacts live beside the checkout in `../validation/`.

## Live turn

The model returns exactly `{"reply":"..."}`. `AgentDecision` forbids extra fields;
Pydantic checks the string and the Service rejects whitespace-only replies. Questions,
reflection, analysis and advice all use the same field. They are contextual choices,
not required steps or serialized strategy labels. Each accepted reply is a completed
ordinary turn; there is no new `waiting_clarification` or one-question ceiling.

The system instructions condition behavior on the user's actual request and history.
They do not prescribe empathy/question/advice ratios, hidden-need classifications or
an empathy-first sequence. An explicit practical request can be answered directly.
Previous assistant interpretations are fallible; corrections take precedence.

## Transport and safety

The single-node LangGraph and existing Chat Completions Provider are retained.
`EmoraAgent.decide` supplies generic format instructions so the unchanged Provider's
legacy default format hint cannot impose its old action/report contract on live chat.
No model call, title call or graph node was added.

Only top-level `reply` string deltas are displayed, without generated headings.
Existing cumulative streamed output checks and final output check remain active.
Final JSON, Pydantic, nonblank and persistence checks still determine success.
As before, already checked stream prefixes can appear before final schema/completion
validation; on final failure the UI replaces the provisional text. Content safety
classification is not proof of clinical/immediate-danger detection accuracy.

## Persistence and compatibility

New `result_json` values contain `reply`; the database schema is unchanged. Old
`AnalysisResult` reports remain readable via `AnalysisResponse.result`'s union and the
legacy renderer. They are not rewritten or backfilled. Completed history contains
the actual natural assistant text; legacy history is reconstructed as its display
Markdown. Existing full-turn context limits, locks and conflict detection remain.

Old pending records retain the clarification endpoint. They may also be answered via
the ordinary create endpoint, under the existing session lock, completing the same
record. Subsequent questions become ordinary new turns. Title generation keeps its
existing endpoint and count; new reply records use original user input instead of a
missing report summary as the source.

This is backward **reading** compatibility, not bidirectional compatibility. External
clients expecting `result.summary` must support `result.reply`. The old application
cannot deserialize newly written reply records. Any eventual deployment needs a
coordinated frontend/backend release and an appropriate database backup; reverting
only code after accepting new records is unsafe. This candidate has not touched the
production database.

## Validation

See the delivered report for results and `../validation/ab_cases.json` for synthetic
quality cases. Pytest and scripted UI outputs demonstrate engineering behavior only.
The default `../validation/run_ab.py` prints an offline plan without reading secrets
or making network calls. Live mode requires human paid-call authorization first,
then the explicit flag and absolute config file path. It uses separate temporary
SQLite databases, the configured real Guard, the same model/effort/format on both
arms, at most 28 chat generations, no title generation and no retries. Do not run
legacy engineering probe scripts as a substitute for this comparison: they answer
different questions and some write to the historical round-one evidence directory.

Existing round-one audit checks that require unchanged Prompt/contract/renderers
intentionally do not apply to this redesign; archived evidence is not overwritten.
