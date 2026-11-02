"""Protocol regressions use synthetic strings, not Guard accuracy claims."""
import pytest

from backend.contracts import QwenSafetyPhase
from backend.safety import GuardProtocolError, parse_qwen3guard_output


@pytest.mark.parametrize("content", [
    "Safety: Safe\nSafety: Unsafe\nCategories: None",
    "Safety: Safe\nCategories: Unknown",
    "Safety: Safe\nCategories: None, Violent",
    "Safety: Safe",
])
def test_ambiguous_or_malformed_classification_fails_closed(content):
    with pytest.raises(GuardProtocolError):
        parse_qwen3guard_output(content,phase=QwenSafetyPhase.INPUT,model_id="simulated")


@pytest.mark.parametrize("content", [
    "Safety: Safe\nCategories: None",
    "Safety: Safe\nCategories: Jailbreak\nRefusal: No",
    "Safety: Safe\nCategories: None\nRefusal: No\nRefusal: Yes",
])
def test_invalid_output_protocol_is_technical_failure(content):
    with pytest.raises(GuardProtocolError):
        parse_qwen3guard_output(content,phase=QwenSafetyPhase.OUTPUT,model_id="simulated")


def test_real_risk_keeps_original_taxonomy():
    result = parse_qwen3guard_output("Safety: Unsafe\nCategories: Violent, Suicide & Self-Harm",
                                    phase=QwenSafetyPhase.INPUT,model_id="simulated")
    assert result.safety.value == "Unsafe"
    assert [c.value for c in result.categories] == ["Violent","Suicide & Self-Harm"]
