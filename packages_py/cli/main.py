"""阶段一命令行入口，暂不暴露工具选择、反馈或授权管理命令。"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid4

import typer

from packages_py.config import Settings, atomic_json, workspace_id
from packages_py.services import Service
from packages_py.storage import StorageError

app = typer.Typer(no_args_is_help=True)


def output(value):
    typer.echo(
        json.dumps(
            value.model_dump(mode="json") if hasattr(value, "model_dump") else value,
            ensure_ascii=False,
            sort_keys=True,
        )
    )


def _service() -> Service:
    """加载当前工作区的模型配置，并从密钥链或已授权的凭据文件读取密钥。"""
    wid = workspace_id()
    base = Path.home() / ".config" / "emotional-agent" / "workspaces" / wid
    config_path = base / "config.json"
    values = {}
    if config_path.exists():
        values = json.loads(config_path.read_text(encoding="utf-8"))
        if values.get("provider") != "openai":
            raise StorageError("provider_unavailable")
        try:
            import keyring

            key = keyring.get_password("emotional-agent", wid)
        except Exception as exc:
            raise StorageError("provider_unavailable") from exc
        credential_file = base / "credential"
        if values.get("credential_ref") == f"workspace-file:{wid}" and credential_file.exists():
            key = credential_file.read_text(encoding="utf-8")
        elif values.get("credential_ref") != f"workspace:{wid}" or key is None:
            raise StorageError("provider_unavailable")
        values.update(
            {
                "model_provider": values.get("provider"),
                "model_name": values.get("model_name"),
                "model_api_key": key,
                "model_timeout_seconds": values.get("timeout_seconds", 60),
                "model_endpoint": values.get("endpoint"),
                "model_max_attempts": values.get("max_attempts", 2),
                "clarification_max_rounds": values.get("clarification_max_rounds", 2),
                "output_max_revisions": values.get("output_max_revisions", 1),
            }
        )
    settings = Settings(**values)
    service = Service(settings)
    identity_path = base / "identity.json"
    if not identity_path.exists():
        subject_id, token = service.storage.create_subject(365 * 86400)
        atomic_json(identity_path, {"subject_id": str(subject_id), "token": token})
    return service


def _subject_id(service: Service) -> UUID:
    """验证本地工作区令牌，并检查其对应用户与本地记录一致。"""
    wid = workspace_id()
    path = Path.home() / ".config" / "emotional-agent" / "workspaces" / wid / "identity.json"
    if not path.exists():
        _service()
    data = json.loads(path.read_text(encoding="utf-8"))
    authenticated = service.storage.authenticate(data["token"])
    if str(authenticated) != data["subject_id"]:
        raise StorageError("unauthorized")
    return authenticated


@app.command()
def onboard(
    provider: str = typer.Option(...),
    model: str = typer.Option(...),
    endpoint: str | None = typer.Option(None),
):
    """配置当前使用的模型服务，密钥保存在代码仓库之外。"""
    api_key = typer.prompt("Provider API key", hide_input=True)
    wid = workspace_id()
    path = Path.home() / ".config" / "emotional-agent" / "workspaces" / wid / "config.json"
    if provider != "openai" or not model.strip():
        typer.echo(json.dumps({"error_code": "provider_unavailable"}))
        raise typer.Exit(code=2)
    try:
        # 保存配置前检查凭据有效性及模型的结构化输出能力。
        from packages_py.providers import OpenAICompatibleProvider
        from packages_py.schemas import SafetyDecision

        adapter = OpenAICompatibleProvider(model, api_key, endpoint, 15, 1)
        probe = adapter._structured(
            SafetyDecision,
            "Return route normal and no risk flags.",
            {"probe": "schema health check"},
        )
        if probe.route not in {"normal", "safety", "uncertain"}:
            raise ValueError("provider probe failed")
    except Exception as exc:
        typer.echo(json.dumps({"error_code": "provider_unavailable"}))
        raise typer.Exit(code=1) from exc
    credential_ref = f"workspace:{wid}"
    try:
        import keyring

        keyring.set_password("emotional-agent", wid, api_key)
    except Exception:
        if not typer.confirm(
            "Keychain unavailable. Store the key in a 0600 file in your user config directory?",
            default=False,
        ):
            raise typer.Exit(code=1)
        secret_path = path.with_name("credential")
        secret_path.write_text(api_key, encoding="utf-8")
        secret_path.chmod(0o600)
        credential_ref = f"workspace-file:{wid}"
    atomic_json(
        path,
        {
            "config_version": "config.v1",
            "provider": provider,
            "model_name": model,
            "endpoint": endpoint,
            "credential_ref": credential_ref,
            "timeout_seconds": 60,
            "max_attempts": 2,
            "clarification_max_rounds": 2,
            "output_max_revisions": 1,
        },
    )
    output({"workspace_id": wid, "provider": provider, "model": model, "status": "saved"})


@app.command()
def analyze(
    text: str | None = typer.Option(None),
    text_file: Path | None = typer.Option(None),
    image: Path | None = typer.Option(None),
    session_id: UUID | None = typer.Option(None),
    request_id: UUID = typer.Option(default_factory=uuid4),
):
    """确认输入文本后执行一次分析。"""
    # 每次仅允许一种输入来源，避免来源优先级不明确或重复处理。
    choices = [value is not None for value in (text, text_file, image)]
    if sum(choices) != 1:
        typer.echo(json.dumps({"error_code": "invalid_request"}))
        raise typer.Exit(code=2)
    try:
        service = _service()
        subject_id = _subject_id(service)
        session = (
            service.new_session(subject_id)
            if session_id is None
            else service.storage.get_session(subject_id, session_id)
        )
        if text_file:
            text = text_file.read_text(encoding="utf-8")
        if image:
            from packages_py.providers import VisionOCRProvider

            text, _ = VisionOCRProvider().extract(str(image))
            typer.echo(
                json.dumps({"confirmation_required": True, "text": text}, ensure_ascii=False)
            )
            # OCR 结果仅是待确认文本，持久化前必须由用户确认。
            if not typer.confirm("Confirm extracted text?"):
                raise typer.Exit(code=2)
        if not image and not typer.confirm("Use this input for analysis?", default=False):
            raise typer.Exit(code=2)
        input_version = service.confirm_input(subject_id, session.session_id, text or "")
        output(
            service.analyze(
                subject_id, session.session_id, input_version.input_version_id, request_id
            )
        )
    except StorageError as exc:
        typer.echo(json.dumps({"error_code": exc.code}))
        raise typer.Exit(code=1 if exc.code not in {"invalid_request", "input_unconfirmed"} else 2)


@app.command()
def resume(
    session_id: UUID,
    run_id: UUID,
    text: str | None = typer.Option(None),
    skip: bool = typer.Option(False),
    request_id: UUID = typer.Option(default_factory=uuid4),
):
    """使用确认后的补充文本或 --skip 恢复暂停的运行。"""
    # 补充回复与跳过澄清互斥，必须且只能选择一种。
    if (text is None) == (not skip):
        typer.echo(json.dumps({"error_code": "invalid_request"}))
        raise typer.Exit(code=2)
    try:
        service = _service()
        subject_id = _subject_id(service)
        from packages_py.schemas import InputResume, SkipResume

        request = (
            SkipResume(request_id=request_id, skip=True)
            if skip
            else InputResume(request_id=request_id, skip=False, input_version_id=uuid4())
        )
        output(service.resume(subject_id, session_id, run_id, request, text=text))
    except StorageError as exc:
        typer.echo(json.dumps({"error_code": exc.code}))
        raise typer.Exit(code=1)


@app.command()
def show(session_id: UUID):
    try:
        service = _service()
        subject_id = _subject_id(service)
        result = service.show(subject_id, session_id)
        if result is None:
            raise StorageError("not_found")
        output(result)
    except StorageError as exc:
        typer.echo(json.dumps({"error_code": exc.code}))
        raise typer.Exit(code=1)


@app.command()
def delete(session_id: UUID, request_id: UUID = typer.Option(default_factory=uuid4)):
    try:
        service = _service()
        subject_id = _subject_id(service)
        output(service.delete(subject_id, session_id, request_id))
    except StorageError as exc:
        typer.echo(json.dumps({"error_code": exc.code}))
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
