"""工具白名单执行器及 LangGraph ToolNode 适配。"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator
from langchain_core.tools import StructuredTool
from langgraph.prebuilt import ToolNode
from pydantic import ConfigDict, create_model

from packages_py.tool_protocol import ToolResult, ToolSpec


@dataclass(frozen=True)
class RegisteredTool:
    spec: ToolSpec
    handler: Callable[[dict, Any], dict]
    authorize: Callable[[Any, str], bool]
    reconcile: Callable[[str, dict, Any], ToolResult | None] | None = None


class ToolExecutor:
    """仅执行已注册的工具，并在调用前检查权限范围和参数结构。"""
    def __init__(self, durable_store=None):
        self._tools: dict[str, RegisteredTool] = {}
        self.durable_store = durable_store

    def register(
        self,
        spec: ToolSpec,
        handler: Callable[[dict, Any], dict],
        authorize: Callable[[Any, str], bool],
        reconcile: Callable[[str, dict, Any], ToolResult | None] | None = None,
    ) -> None:
        if spec.name in self._tools:
            raise ValueError("tool already registered")
        self._tools[spec.name] = RegisteredTool(spec, handler, authorize, reconcile)

    def execute(
        self, name: str, payload: dict, auth_context: Any, idempotency_key: str | None = None
    ) -> ToolResult:
        registered = self._tools.get(name)
        if registered is None:
            return ToolResult(tool_name=name, ok=False, error_code="tool_not_allowed")
        spec = registered.spec
        try:
            if not registered.authorize(auth_context, spec.auth_scope):
                return ToolResult(tool_name=name, ok=False, error_code="unauthorized")
            Draft202012Validator(spec.input_schema).validate(payload)

            def invoke():
                started = time.monotonic()
                output = registered.handler(payload, auth_context)
                Draft202012Validator(spec.output_schema).validate(output)
                timed_out = time.monotonic() - started > spec.timeout_seconds
                return ToolResult(
                    tool_name=name,
                    ok=not timed_out,
                    output=None if timed_out else output,
                    error_code="tool_timeout" if timed_out else None,
                ).validate_shape()

            # 有副作用的调用必须持久化去重，防止调用方超时重试造成重复写入。
            if spec.side_effect != "none":
                if not idempotency_key or not spec.idempotent or self.durable_store is None:
                    return ToolResult(
                        tool_name=name, ok=False, error_code="tool_idempotency_required"
                    )
                return self.durable_store.execute_tool_once(
                    f"{name}:{idempotency_key}",
                    invoke,
                    lambda: (
                        registered.reconcile(idempotency_key, payload, auth_context)
                        if registered.reconcile
                        else None
                    ),
                    payload_hash=hashlib.sha256(
                        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
                    ).hexdigest(),
                )
            return invoke()
        except Exception:
            return ToolResult(
                tool_name=name, ok=False, error_code="tool_execution_failed"
            ).validate_shape()

    def tool_node(self, auth_context: Any) -> ToolNode:
        """将白名单执行器适配为 LangGraph 工具调用节点。"""
        tools = []
        for name in self._tools:
            registered = self._tools[name]
            schema = registered.spec.input_schema
            fields = {}
            type_map = {
                "string": str,
                "integer": int,
                "number": float,
                "boolean": bool,
                "object": dict,
                "array": list,
            }
            for field_name, field_schema in schema.get("properties", {}).items():
                field_type = type_map.get(field_schema.get("type"), dict)
                required = field_name in schema.get("required", [])
                fields[field_name] = (field_type, ... if required else None)
            args_model = create_model(
                f"{name.title().replace('_', '')}Args",
                __config__=ConfigDict(extra="forbid"),
                **fields,
            )

            def invoke(tool_name=name, **payload):
                result = self.execute(tool_name, payload, auth_context)
                if not result.ok:
                    raise RuntimeError(result.error_code)
                return result.output

            tools.append(
                StructuredTool.from_function(
                    func=invoke,
                    name=name,
                    description=registered.spec.description,
                    args_schema=args_model,
                )
            )
        from langgraph.graph import END, START, StateGraph

        builder = StateGraph(dict)
        builder.add_node("tools", ToolNode(tools, handle_tool_errors=False))
        builder.add_edge(START, "tools")
        builder.add_edge("tools", END)
        return builder.compile()


def register_get_run_status(executor: ToolExecutor, repository, *, subject_id, session_id) -> None:
    """注册只读运行状态查询工具，并限定到已授权用户及会话。"""
    spec = ToolSpec(
        name="get_run_status",
        description="Read status of a run in the authorized session.",
        input_schema={
            "type": "object",
            "properties": {"run_id": {"type": "string", "format": "uuid"}},
            "required": ["run_id"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "session_id": {"type": "string"},
                "status": {"type": "string"},
                "error_code": {"type": ["string", "null"]},
            },
            "required": ["run_id", "session_id", "status", "error_code"],
            "additionalProperties": False,
        },
        version="1",
        timeout_seconds=5,
        auth_scope="session:read",
    )

    def authorized(context, scope):
        return (
            scope == "session:read"
            and context.get("subject_id") == str(subject_id)
            and context.get("session_id") == str(session_id)
        )

    def read(payload, _context):
        run, _ = repository.get_run(subject_id, session_id, payload["run_id"])
        return {
            "run_id": str(run.run_id),
            "session_id": str(run.session_id),
            "status": run.status,
            "error_code": run.error_code,
        }

    executor.register(spec, read, authorized)
