"""LangGraph 工作流的编译、启动与恢复入口。"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager

from langgraph.checkpoint.postgres import PostgresSaver
from psycopg import Connection
from psycopg.rows import dict_row

from packages_py.compatibility import check_compatibility
from packages_py.workflow import build_graph


@contextmanager
def checkpoint_connection(checkpoint_url: str, checkpoint_schema: str):
    """通过 search_path 隔离检查点所在的数据库模式。"""
    raw_url = checkpoint_url.replace("postgresql+psycopg://", "postgresql://", 1)
    with Connection.connect(
        raw_url,
        autocommit=True,
        prepare_threshold=0,
        row_factory=dict_row,
        options=f"-c search_path={checkpoint_schema}",
    ) as connection:
        yield connection


class WorkflowRuntime:
    """管理一次服务调用使用的工作流及 PostgreSQL 检查点连接。"""
    def __init__(self, repository, provider, checkpoint_url: str, checkpoint_schema: str):
        self.repository = repository
        self.provider = provider
        if not checkpoint_schema.replace("_", "").isalnum():
            raise ValueError("invalid checkpoint schema")
        raw_url = checkpoint_url.replace("postgresql+psycopg://", "postgresql://", 1)
        self._stack = ExitStack()
        self.connection = self._stack.enter_context(
            Connection.connect(
                raw_url,
                autocommit=True,
                prepare_threshold=0,
                row_factory=dict_row,
                options=f"-c search_path={checkpoint_schema}",
            )
        )
        self.checkpointer = PostgresSaver(self.connection)
        self.checkpointer.setup()
        self.graph = build_graph(repository=repository, provider=provider).compile(
            checkpointer=self.checkpointer
        )

    def start(self, state: dict):
        state.setdefault("workflow_state_schema_version", "stage1.state.v1")
        state.setdefault("graph_version", "stage1.graph.v1")
        state.setdefault("runtime_version", "stage1.runtime.v1")
        config = {"configurable": {"thread_id": state["thread_id"]}}
        return self.graph.invoke(state, config=config)

    def resume(self, command: dict):
        config = {"configurable": {"thread_id": command["thread_id"]}}
        state = self.graph.get_state(config).values
        if not state or state.get("phase") != "waiting_clarification":
            raise RuntimeError("run_not_waiting")
        # 向 LangGraph 提交恢复命令前，先检查序列化状态的运行时兼容性。
        decision = check_compatibility(command["verified_state"], command["runtime_manifest"])
        if decision["action"] != "allowed":
            raise RuntimeError("checkpoint_incompatible")
        from langgraph.types import Command

        return self.graph.invoke(Command(resume=command["request"]), config=config)

    def close(self):
        self._stack.close()
