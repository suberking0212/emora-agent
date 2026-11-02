"""基于固定词表的安全筛查与安全回复策略。"""

from packages_py.schemas import SafetyDecision

_RISK_TERMS = {
    "self_harm": ("自杀", "自残", "不想活", "结束生命"),
    "violence": ("家暴", "威胁我", "跟踪我", "性暴力", "无法安全离开"),
    "harmful_request": ("跟踪对方", "报复他", "报复她", "套取隐私", "操纵对方"),
}


def classify_text(text: str) -> SafetyDecision:
    """在模型理解之前，先执行基于固定词表的首轮安全筛查。"""
    found = [name for name, terms in _RISK_TERMS.items() if any(term in text for term in terms)]
    return SafetyDecision(route="safety" if found else "normal", risk_flags=found)


def safe_message(risk_flags: list[str]) -> str:
    """按风险类型选择简短回复，优先处理当前安全问题。"""
    if "self_harm" in risk_flags:
        return "如果你或他人正处于立即危险中，请马上联系当地紧急服务，并联系一位可信任的人陪伴你。"
    if "violence" in risk_flags:
        return "先把人身安全放在首位；尽量联系可信任的人或当地专业支持，避免在不安全时直接对质。"
    return "我不能帮助跟踪、威胁或操纵他人；可以改为准备一段尊重边界、表达自身需要的话。"
