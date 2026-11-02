import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, text


class Database:
    def __init__(self, url: str):
        if url.startswith("sqlite:///"):
            path = url.removeprefix("sqlite:///")
            if path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            self.engine = create_engine(url, connect_args={"check_same_thread": False})
        else:
            raise ValueError("only SQLite database URLs are supported")

    def init_db(self) -> None:
        with self.engine.begin() as connection:
            connection.execute(text("""CREATE TABLE IF NOT EXISTS analyses (
                id TEXT PRIMARY KEY, original_input TEXT NOT NULL,
                clarification_question TEXT, clarification_answer TEXT, result_json TEXT,
                status TEXT NOT NULL, deep_thinking INTEGER NOT NULL DEFAULT 0,
                reasoning_effort TEXT NOT NULL DEFAULT 'medium',
                input_safety_level TEXT,
                input_safety_categories_json TEXT NOT NULL DEFAULT '[]', error_code TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )"""))
            columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(analyses)")}
            if "deep_thinking" not in columns:
                connection.execute(text(
                    "ALTER TABLE analyses ADD COLUMN deep_thinking INTEGER NOT NULL DEFAULT 0"
                ))
            if "reasoning_effort" not in columns:
                connection.execute(text(
                    "ALTER TABLE analyses ADD COLUMN reasoning_effort TEXT NOT NULL DEFAULT 'medium'"
                ))

    @staticmethod
    def _decode(row):
        if row is None:
            return None
        record = dict(row._mapping)
        record["result"] = json.loads(record.pop("result_json")) if record["result_json"] else None
        record["safety_categories"] = json.loads(record.pop("input_safety_categories_json"))
        record["deep_thinking"] = bool(record["deep_thinking"])
        return record

    def create(self, original_input: str, status: str, guard=None, clarification_question=None,
               result=None, error_code=None, deep_thinking: bool = False,
               reasoning_effort: str = "medium"):
        now = datetime.now(timezone.utc).isoformat()
        record_id = uuid4().hex
        with self.engine.begin() as connection:
            connection.execute(text("""INSERT INTO analyses
                (id, original_input, clarification_question, status, deep_thinking, reasoning_effort, input_safety_level,
                 input_safety_categories_json, result_json, error_code, created_at, updated_at)
                VALUES (:id, :input, :question, :status, :deep_thinking, :reasoning_effort, :level, :categories, :result, :error, :created, :updated)"""), {
                "id": record_id, "input": original_input, "question": clarification_question,
                "status": status, "deep_thinking": int(deep_thinking),
                "reasoning_effort": reasoning_effort,
                "level": guard.safety.value if guard else None,
                "categories": json.dumps([item.value for item in guard.categories]) if guard else "[]",
                "result": json.dumps(result, ensure_ascii=False) if result else None,
                "error": error_code, "created": now, "updated": now,
            })
            row = connection.execute(text("SELECT * FROM analyses WHERE id=:id"), {"id": record_id}).first()
        return self._decode(row)

    def update(self, record_id: str, *, status: str, guard=None, clarification_question=None,
               clarification_answer=None, result=None, error_code=None, deep_thinking: bool | None = None,
               reasoning_effort: str | None = None):
        now = datetime.now(timezone.utc).isoformat()
        params = {
            "id": record_id, "status": status, "question": clarification_question,
            "deep_thinking": int(deep_thinking) if deep_thinking is not None else None,
            "reasoning_effort": reasoning_effort,
            "answer": clarification_answer, "result": json.dumps(result, ensure_ascii=False) if result else None,
            "error": error_code, "updated": now,
            "level": guard.safety.value if guard else None,
            "categories": json.dumps([item.value for item in guard.categories]) if guard else "[]",
        }
        with self.engine.begin() as connection:
            connection.execute(text("""UPDATE analyses SET status=:status,
                clarification_question=COALESCE(:question, clarification_question),
                clarification_answer=COALESCE(:answer, clarification_answer),
                deep_thinking=COALESCE(:deep_thinking, deep_thinking),
                reasoning_effort=COALESCE(:reasoning_effort, reasoning_effort),
                result_json=:result, error_code=:error,
                input_safety_level=COALESCE(:level, input_safety_level),
                input_safety_categories_json=CASE WHEN :level IS NULL THEN input_safety_categories_json ELSE :categories END,
                updated_at=:updated WHERE id=:id"""), params)
            row = connection.execute(text("SELECT * FROM analyses WHERE id=:id"), {"id": record_id}).first()
        return self._decode(row)

    def get(self, record_id: str):
        with self.engine.connect() as connection:
            return self._decode(connection.execute(text("SELECT * FROM analyses WHERE id=:id"), {"id": record_id}).first())

    def list(self, limit: int = 100):
        with self.engine.connect() as connection:
            rows = connection.execute(text("SELECT * FROM analyses ORDER BY created_at DESC LIMIT :limit"), {"limit": limit}).all()
        return [self._decode(row) for row in rows]

    def close(self):
        self.engine.dispose()
