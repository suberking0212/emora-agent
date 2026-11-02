"""Read-only postflight: no network, config, secrets or production database access."""
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
R = Path(__file__).resolve().parents[1]
P = Path('/Users/laosun/Documents/emotional solution agent/emora-mvp-standalone')
B, C = R/'baseline', R/'candidate'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
manifest = json.loads((R/'source-manifest.json').read_text())
boundary = json.loads((R/'validation/boundary-checks.json').read_text())
boundary['production_manifest_differences'] = [p for p,h in manifest.items() if sha(P/p) != h]
files = [p for p in B.rglob('*') if p.is_file() and '__pycache__' not in p.parts]
boundary['snapshot_files_compared'] = len(files)
boundary['production_vs_snapshot_differences'] = [str(p.relative_to(B)) for p in files if not (P/p.relative_to(B)).is_file() or sha(p) != sha(P/p.relative_to(B))]
for name in boundary['protected_candidate_equal_baseline']:
    boundary['protected_candidate_equal_baseline'][name] = sha(B/name) == sha(C/name)
boundary['no_env_in_candidate'] = not (C/'.env').exists()
rows, summary = [], {}
for arm in ('baseline','candidate'):
    paths = sorted((R/'validation/live-results').glob(f'*-{arm}.json'))
    records = [r for p in paths for r in json.loads(p.read_text())['records']]
    statuses = Counter(r['response']['status'] for r in records)
    summary[arm] = {'cases':len(paths), 'turns':len(records), 'statuses':dict(statuses),
                    'generation_calls_from_execution_path':sum(r['response']['status'] in ('completed','waiting_clarification') for r in records),
                    'candidate_stream_equals_final': all(r['safe_stream_text'] == r['response']['result']['reply'] for r in records if arm == 'candidate' and r['response']['status'] == 'completed') if arm == 'candidate' else None}
summary.update({'requested_model':'gpt-5.6-terra', 'effort':'medium','response_format':'json_object',
 'title_calls':0,'automatic_retries':0,'paid_generation_calls_from_execution_path':26,
 'authorization_limit':28,'paid_count_evidence':'26 successful service turns require exactly one graph/provider call each; 2 input-blocked turns bypass graph; no retries or titles. Not a gateway billing statement.',
 'telemetry_limitation':'Outer provider_metrics collector is shadowed by AnalysisService._operation. Empty provider_metrics and progress generation_attempts=0 are a harness observation bug, not zero calls. Per-call usage, returned_model, finish_reason and duration were not retained. Success requires Provider finish_reason stop; no raw usage or bill can be reconstructed.',
 'usage_tokens':None, 'billed_amount':None})
(R/'validation/live-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
boundary.update({'paid_generations':26,'paid_generations_evidence':'validation/live-summary.json; count from execution path, not billing telemetry','human_authorization_limit':28,'paid_model_configuration_read_in_memory':True})
(R/'validation/boundary-checks.json').write_text(json.dumps(boundary,ensure_ascii=False,indent=2))
blind = json.loads((R/'validation/live-results/blind-review.json').read_text())
key = {(x['case_id'],x['label']):x['arm'] for x in json.loads((R/'validation/live-results/review-key.json').read_text())}
lines = ['# 真实合成案例逐字对照','', '一次采样；版本标签曾隐藏，但格式无法盲化。本文件揭示版本。评价见实施与验证报告。','']
for case in json.loads((R/'validation/ab_cases.json').read_text()):
    lines += [f"## {case['id']}", '']
    for item in sorted((x for x in blind if x['case_id']==case['id']), key=lambda x:key[(x['case_id'],x['label'])]):
        lines += [f"### {key[(item['case_id'],item['label'])]}", '']
        for i,t in enumerate(item['turns'],1):
            lines += [f"**第 {i} 轮用户：** {t['user']}",'',f"**状态：** {t['status']}",'', t['reply'],'']
(R/'validation/真实回复对照.md').write_text('\n'.join(lines))
print(json.dumps({'summary':summary,'production_changed':boundary['production_manifest_differences'],'snapshot_differences':boundary['production_vs_snapshot_differences'],'snapshot_files':len(files)},ensure_ascii=False,indent=2))
