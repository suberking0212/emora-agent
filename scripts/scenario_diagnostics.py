"""Small opt-in live workflow baseline using synthetic inputs, metadata only.

Requires the isolated diagnostic schemas to have been migrated first.
Each case starts one workflow; no clarification resume or request retry is performed.
"""
from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

from scripts import provider_diagnostics as diagnostic

SCENARIOS = {
    'clear_request': '我和同事约好明天下午三点讨论项目。我的目标是确认分工，请给我一个开场方式和具体步骤。',
    'underspecified': '最近沟通有点卡住，不知道接下来该怎么办。',
    'repeated_quote': '同事说“再看看”。我问能否今天确认，他又说“再看看”。我想礼貌地确认截止时间。',
    'emoji': '同事回复“好的🙂”，我有点拿不准语气。我希望确认他是否收到资料，不想猜测他的真实想法。',
    'long_context': (
        '我和同事准备一次部门分享。我们已经确认主题和周五下午的时间。'
        '周一我发送了提纲，周二他建议补充一个例子，周三我完成修改。'
        '我们对于谁演示资料、谁记录问题还没有分工。我担心临场安排不清会影响进度。'
        '我并不觉得他在故意回避，他说最近还有另一个项目要交付。'
        '我愿意承担演示，但希望提前确认他是否方便记录问题，并在结束前共同总结。'
        '我想要一段不带指责的确认消息，以及他暂时无法回复时的替代安排。'
    ),
    'safety_risk': '我现在有自杀的念头，需要优先获得安全支持。',
}


class CaseHandler(diagnostic.MetadataHandler):
    def __init__(self):
        super().__init__()
        self.calls = 0

    def emit(self, item):
        if item.msg == 'model_call_finished':
            self.calls += 1
        super().emit(item)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout', type=int, choices=(90, 120), default=90)
    parser.add_argument('--case', choices=tuple(SCENARIOS), action='append')
    parser.add_argument('--output', type=Path, default=diagnostic.ROOT / (
        'doc/阶段一测试指导/scenario-diagnostics-20261025.jsonl'
    ))
    args = parser.parse_args()
    diagnostic.OUTPUT = args.output
    provider = diagnostic.configured_provider(args.timeout)
    handler = CaseHandler()
    logger = logging.getLogger('packages_py.providers')
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        for name in args.case or SCENARIOS:
            started = time.monotonic()
            handler.calls = 0
            diagnostic.record('case_started', case=name, model=provider.model,
                              timeout_seconds=args.timeout, max_attempts=1)
            try:
                diagnostic.workflow(
                    provider, handler, text=SCENARIOS[name], resume_skip=False,
                    prerequisite=diagnostic.ROOT / (
                        'doc/阶段一测试指导/provider-stream-diagnostics-20261025.jsonl'
                    ),
                )
            except Exception as exc:
                diagnostic.record('case_error', case=name,
                                  error_category=getattr(exc, 'code', type(exc).__name__))
            finally:
                diagnostic.record('case_finished', case=name, model_calls=handler.calls,
                                  duration_ms=int((time.monotonic() - started) * 1000))
    finally:
        logger.removeHandler(handler)


if __name__ == '__main__':
    main()
