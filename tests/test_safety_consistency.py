from scripts.safety_consistency import summarize


def test_summary_counts_false_blocks_misses_fluctuations_and_paired_post():
    rows = [
        {
            "event": "classification",
            "model": "m",
            "case": "ordinary",
            "phase": "pre",
            "arm": "none",
            "expected": "normal",
            "repeat": 1,
            "status": "ok",
            "route": "normal",
        },
        {
            "event": "classification",
            "model": "m",
            "case": "ordinary",
            "phase": "post",
            "arm": "model",
            "expected": "normal",
            "repeat": 1,
            "status": "ok",
            "route": "uncertain",
        },
        {
            "event": "classification",
            "model": "m",
            "case": "ordinary",
            "phase": "post",
            "arm": "reference",
            "expected": "normal",
            "repeat": 1,
            "status": "ok",
            "route": "normal",
        },
        {
            "event": "classification",
            "model": "m",
            "case": "risk",
            "phase": "pre",
            "arm": "none",
            "expected": "safety",
            "repeat": 1,
            "status": "ok",
            "route": "normal",
        },
        {
            "event": "classification",
            "model": "m",
            "case": "risk",
            "phase": "pre",
            "arm": "none",
            "expected": "safety",
            "repeat": 2,
            "status": "ok",
            "route": "safety",
        },
    ]
    report = summarize(rows)
    ordinary = next(x for x in report["groups"] if x["case"] == "ordinary")
    assert ordinary["ordinary_blocks"] == 1
    paired = report["paired_post"][0]
    assert paired["different"] == 1
    assert paired["model_uncertain_reference_normal"] == 1
    risk = next(x for x in report["groups"] if x["case"] == "risk")
    assert risk["risk_normal_misses"] == 1
    assert risk["fluctuates"] is True


def test_fixed_payloads_repeated_and_counterfactual_risk_post_is_measured():
    from packages_py.schemas import ContextUnderstanding, SafetyDecision
    from scripts.safety_consistency import (
        CASES,
        context_for,
        reference_understanding,
        run,
    )

    class Provider:
        model = "fake"

        def __init__(self):
            self.calls = []

        def understand(self, *, context):
            for name, case in CASES.items():
                if case["text"] == context.text and case["expected"] == "normal":
                    return reference_understanding(name, case, context)
            return ContextUnderstanding()

        def classify_safety(self, *, context, phase, understanding=None):
            self.calls.append(
                (
                    context.model_dump(mode="json"),
                    phase,
                    understanding.model_dump(mode="json") if understanding else None,
                )
            )
            return SafetyDecision(route="normal")

    provider = Provider()
    run(provider, 3, lambda *args, **kwargs: None, "candidate")
    assert len(provider.calls) == 45
    for name, case in CASES.items():
        ctx = context_for(name, case["text"]).model_dump(mode="json")
        calls = [c for c in provider.calls if c[0] == ctx]
        assert len([c for c in calls if c[1] == "pre"]) == 3
        assert len([c for c in calls if c[1] == "post"]) == (
            6 if case["expected"] == "normal" else 3
        )
        assert all(c[0] == ctx for c in calls)
        # Each post arm must use a single frozen understanding, not regenerate it.
        assert len({str(c[2]) for c in calls if c[1] == "post"}) <= 2
