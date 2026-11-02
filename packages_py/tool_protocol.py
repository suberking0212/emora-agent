"""独立于模型服务商的严格工具请求与结果协议。"""

from typing import Literal

from pydantic import Field

from packages_py.schemas import Contract


class ToolSpec(Contract):
    """声明工具版本、输入输出结构、权限范围及副作用策略。"""
    name: str
    description: str
    input_schema: dict
    output_schema: dict
    version: str
    timeout_seconds: int = Field(gt=0)
    auth_scope: str
    side_effect: Literal["none", "write", "external"] = "none"
    idempotent: bool = True


class ToolResult(Contract):
    """统一工具结果结构：成功时携带输出，失败时仅携带错误码。"""
    tool_name: str
    ok: bool
    output: dict | None = None
    error_code: str | None = None

    def validate_shape(self):
        if self.ok != (self.output is not None and self.error_code is None):
            raise ValueError("invalid tool result shape")
        if not self.ok and (self.error_code is None or self.output is not None):
            raise ValueError("invalid tool result shape")
        return self
