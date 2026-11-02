"""Repeatable, opt-in engineering verification; never uses the application database."""
import argparse
import ast
import difflib
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tarfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "research/engineering-remediation-round1"


def write_json(name, value):
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def source_files():
    roots = [ROOT / name for name in ("backend", "frontend", "scripts", "doc")]
    files = [p for base in roots for p in base.rglob("*") if p.is_file()
             and "__pycache__" not in p.parts and "data" not in p.relative_to(ROOT).parts
             and p.suffix in {".py", ".js", ".css", ".html", ".md", ".sh"}]
    files.extend(p for p in ROOT.iterdir() if p.is_file() and
                 (p.name in {".env.example", ".gitignore", "pyproject.toml", "uv.lock"}
                  or p.suffix in {".md", ".sh"}))
    return sorted(set(files))


def baseline():
    if (EVIDENCE / "baseline.json").exists():
        raise SystemExit("Baseline already exists; refusing to replace it")
    files = source_files()
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    with tarfile.open(EVIDENCE / "baseline-source.tar.gz", "w:gz") as archive:
        for path in files:
            archive.add(path, arcname=str(path.relative_to(ROOT)))
    status = subprocess.check_output(["git", "status", "--short", "--branch"], cwd=ROOT, text=True)
    write_json("baseline.json", {
        "captured_at": datetime.now(timezone.utc).isoformat(), "git_status": status,
        "files": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
        "excluded": ["secrets", "databases", "logs", "virtualenvs", "research artifacts"],
    })
    print(f"Baseline: {len(files)} source/document files; no credentials or user data")


def audit():
    baseline_data = json.loads((EVIDENCE / "baseline.json").read_text())
    with tarfile.open(EVIDENCE / "baseline-source.tar.gz") as archive:
        original = {member.name: archive.extractfile(member).read().decode()
                    for member in archive.getmembers() if member.isfile()}
    current = {str(path.relative_to(ROOT)): path.read_text() for path in source_files()}

    def node(source, name, parent=None):
        tree = ast.parse(source)
        if parent:
            tree = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == parent)
        return ast.dump(next(n for n in tree.body if getattr(n, "name", None) == name))

    def same_node(path, name, parent=None):
        return node(original[path], name, parent) == node(current[path], name, parent)

    def assignment_values(source, name):
        return sorted(ast.dump(n.value) for n in ast.walk(ast.parse(source))
                      if isinstance(n, ast.Assign) and any(
                          isinstance(t, ast.Name) and t.id == name for t in n.targets))

    def renderer(source):
        return source.split("function resultText(result) {", 1)[1].split("function statusText", 1)[0]

    checks = {
        "system_prompt_unchanged": original["backend/prompts/system.md"] == current["backend/prompts/system.md"],
        "analysis_result_unchanged": same_node("backend/contracts.py", "AnalysisResult"),
        "agent_decision_unchanged": same_node("backend/contracts.py", "AgentDecision"),
        "stream_formatter_unchanged": same_node("backend/services.py", "_format", "GuardedTextStream"),
        "sse_protocol_unchanged": original["backend/streaming.py"] == current["backend/streaming.py"],
        "graph_constructor_unchanged": same_node("backend/agent.py", "__init__", "EmoraAgentGraph"),
        "qwen_guard_unchanged": original["backend/safety.py"] == current["backend/safety.py"],
        "frontend_result_renderer_unchanged": renderer(original["frontend/static/app.js"]) == renderer(current["frontend/static/app.js"]),
        "public_summary_prompt_unchanged": assignment_values(original["backend/provider.py"], "PUBLIC_ANALYSIS_PROMPT") == assignment_values(current["backend/provider.py"], "PUBLIC_ANALYSIS_PROMPT"),
        "deep_mode_prompt_unchanged": assignment_values(original["backend/provider.py"], "deep_thinking_prompt") == assignment_values(current["backend/provider.py"], "deep_thinking_prompt"),
    }
    status = subprocess.check_output(["git", "status", "--short", "--branch"], cwd=ROOT, text=True)
    checks["parent_git_status_matches_baseline"] = status == baseline_data["git_status"]
    changed = [path for path in sorted(original) if original[path] != current.get(path)]
    added = sorted(set(current) - set(original))
    patch = "".join("".join(difflib.unified_diff(
        original.get(path, "").splitlines(keepends=True), current.get(path, "").splitlines(keepends=True),
        fromfile="baseline/"+path, tofile="current/"+path)) for path in changed+added)
    (EVIDENCE / "source-changes.patch").write_text(patch)
    write_json("boundary-checks.json", checks)
    write_json("final-source-manifest.json", {
        "at": datetime.now(timezone.utc).isoformat(), "changed": changed, "added": added,
        "files": {p: hashlib.sha256(v.encode()).hexdigest() for p,v in current.items()},
        "passed": all(checks.values()),
    })
    print(json.dumps({"checks": checks, "changed": changed, "added": added}, indent=2))
    if not all(checks.values()):
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["baseline", "tests", "provider", "real", "audit"])
    args = parser.parse_args()
    if args.command == "baseline":
        baseline()
    elif args.command == "audit":
        audit()
    elif args.command == "tests":
        raise SystemExit(subprocess.call([sys.executable,"-m","pytest","backend/tests",
            "--junitxml="+str(EVIDENCE / "final-pytest.xml")],cwd=ROOT))
    elif args.command == "provider":
        raise SystemExit(subprocess.call([sys.executable,str(ROOT / "scripts/probe_provider.py")],cwd=ROOT))
    elif args.command == "real":
        raise SystemExit(subprocess.call([sys.executable,str(ROOT / "scripts/verify_runtime.py")],cwd=ROOT))


if __name__ == "__main__":
    main()
