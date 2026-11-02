"""显式检查恢复兼容性，不恢复无法确认兼容的检查点状态。"""

from typing import Literal, TypedDict


class CompatibilityDecision(TypedDict):
    action: Literal["allowed", "migration_required", "incompatible"]
    reason: str
    source_version: str
    target_version: str
    runtime_compatible: bool
    data_schema_compatible: bool
    checkpoint_compatible: bool
    dependency_lock_compatible: bool


def check_compatibility(source: dict[str, str], target: dict[str, str]) -> CompatibilityDecision:
    # 检查点包含影响工作流执行的状态；只有相关运行时版本均明确且一致，
    # 才允许恢复，避免用不同版本的图或依赖解释旧状态。
    keys = (
        "runtime_version",
        "graph_version",
        "workflow_state_schema_version",
        "dependency_lock_hash",
        "checkpoint_compatibility_profile",
    )
    ok = all(source.get(key) and source.get(key) == target.get(key) for key in keys)
    return {
        "action": "allowed" if ok else "incompatible",
        "reason": "all pinned runtime dimensions match" if ok else "checkpoint_incompatible",
        "source_version": source.get("workflow_state_schema_version", "unknown"),
        "target_version": target.get("workflow_state_schema_version", "unknown"),
        "runtime_compatible": source.get("runtime_version") == target.get("runtime_version"),
        "data_schema_compatible": source.get("data_schema_version")
        == target.get("data_schema_version"),
        "checkpoint_compatible": source.get("checkpoint_compatibility_profile")
        == target.get("checkpoint_compatibility_profile"),
        "dependency_lock_compatible": source.get("dependency_lock_hash")
        == target.get("dependency_lock_hash"),
    }
