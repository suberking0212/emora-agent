import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, text


class SessionConflict(RuntimeError):
    pass


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
                status TEXT NOT NULL,
                reasoning_effort TEXT NOT NULL DEFAULT 'medium',
                session_id TEXT, turn_index INTEGER NOT NULL DEFAULT 0,
                input_safety_level TEXT,
                input_safety_categories_json TEXT NOT NULL DEFAULT '[]', error_code TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )"""))
            connection.execute(text("""CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY, title TEXT, pinned INTEGER NOT NULL DEFAULT 0,
                archived INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )"""))
            session_columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(sessions)")}
            if "title" not in session_columns:
                connection.execute(text("ALTER TABLE sessions ADD COLUMN title TEXT"))
            columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(analyses)")}
            if "reasoning_effort" not in columns:
                connection.execute(text(
                    "ALTER TABLE analyses ADD COLUMN reasoning_effort TEXT NOT NULL DEFAULT 'medium'"
                ))
            if "session_id" not in columns:
                connection.execute(text("ALTER TABLE analyses ADD COLUMN session_id TEXT"))
            if "turn_index" not in columns:
                connection.execute(text("ALTER TABLE analyses ADD COLUMN turn_index INTEGER NOT NULL DEFAULT 0"))
            connection.execute(text("UPDATE analyses SET session_id=id WHERE session_id IS NULL"))
            connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS analyses_session_turn ON analyses(session_id, turn_index)"))
            connection.execute(text("""INSERT OR IGNORE INTO sessions(session_id, created_at, updated_at)
                SELECT session_id, MIN(created_at), MAX(updated_at) FROM analyses GROUP BY session_id"""))

    @staticmethod
    def _decode(row):
        if row is None:
            return None
        record = dict(row._mapping)
        record["result"] = json.loads(record.pop("result_json")) if record["result_json"] else None
        record["safety_categories"] = json.loads(record.pop("input_safety_categories_json"))
        return record

    def create(self, original_input: str, status: str, guard=None, clarification_question=None,
               result=None, error_code=None, reasoning_effort: str = "medium", session_id: str | None = None,
               expected_turn: int | None = None):
        now = datetime.now(timezone.utc).isoformat()
        record_id = uuid4().hex
        session_id = session_id or record_id
        with self.engine.begin() as connection:
            turn_index = connection.execute(text(
                "SELECT COALESCE(MAX(turn_index), -1)+1 FROM analyses WHERE session_id=:session"
            ), {"session": session_id}).scalar_one()
            if expected_turn is not None and expected_turn != turn_index:
                raise SessionConflict("session_changed")
            connection.execute(text("""INSERT INTO analyses
                (id, session_id, turn_index, original_input, clarification_question, status, reasoning_effort, input_safety_level,
                 input_safety_categories_json, result_json, error_code, created_at, updated_at)
                VALUES (:id, :session, :turn, :input, :question, :status, :reasoning_effort, :level, :categories, :result, :error, :created, :updated)"""), {
                "id": record_id, "input": original_input, "question": clarification_question,
                "session": session_id, "turn": turn_index,
                "status": status,
                "reasoning_effort": reasoning_effort,
                "level": guard.safety.value if guard else None,
                "categories": json.dumps([item.value for item in guard.categories]) if guard else "[]",
                "result": json.dumps(result, ensure_ascii=False) if result else None,
                "error": error_code, "created": now, "updated": now,
            })
            row = connection.execute(text("SELECT * FROM analyses WHERE id=:id"), {"id": record_id}).first()
            connection.execute(text("""INSERT OR IGNORE INTO sessions(session_id, created_at, updated_at)
                VALUES (:session, :created, :updated)"""), {"session": session_id, "created": now, "updated": now})
            connection.execute(text("UPDATE sessions SET updated_at=:updated WHERE session_id=:session"),
                               {"session": session_id, "updated": now})
        return self._decode(row)

    def update(self, record_id: str, *, status: str, guard=None, clarification_question=None,
               clarification_answer=None, result=None, error_code=None,
               reasoning_effort: str | None = None, expected_updated_at: str | None = None):
        now = datetime.now(timezone.utc).isoformat()
        params = {
            "id": record_id, "status": status, "question": clarification_question,
            "reasoning_effort": reasoning_effort,
            "answer": clarification_answer, "result": json.dumps(result, ensure_ascii=False) if result else None,
            "error": error_code, "updated": now,
            "level": guard.safety.value if guard else None,
            "categories": json.dumps([item.value for item in guard.categories]) if guard else "[]",
            "expected": expected_updated_at,
        }
        with self.engine.begin() as connection:
            changed = connection.execute(text("""UPDATE analyses SET status=:status,
                clarification_question=COALESCE(:question, clarification_question),
                clarification_answer=COALESCE(:answer, clarification_answer),
                reasoning_effort=COALESCE(:reasoning_effort, reasoning_effort),
                result_json=:result, error_code=:error,
                input_safety_level=COALESCE(:level, input_safety_level),
                input_safety_categories_json=CASE WHEN :level IS NULL THEN input_safety_categories_json ELSE :categories END,
                updated_at=:updated WHERE id=:id AND
                (:expected IS NULL OR (updated_at=:expected AND status='waiting_clarification'))"""), params)
            if expected_updated_at is not None and changed.rowcount != 1:
                raise SessionConflict("clarification_changed")
            connection.execute(text("""UPDATE sessions SET updated_at=:updated
                WHERE session_id=(SELECT session_id FROM analyses WHERE id=:id)"""),
                {"updated": now, "id": record_id})
            row = connection.execute(text("SELECT * FROM analyses WHERE id=:id"), {"id": record_id}).first()
        return self._decode(row)

    def get(self, record_id: str):
        with self.engine.connect() as connection:
            return self._decode(connection.execute(text("SELECT * FROM analyses WHERE id=:id"), {"id": record_id}).first())

    def list(self, limit: int = 100):
        with self.engine.connect() as connection:
            rows = connection.execute(text("SELECT * FROM analyses ORDER BY created_at DESC LIMIT :limit"), {"limit": limit}).all()
        return [self._decode(row) for row in rows]

    def list_sessions(self, limit: int = 100):
        with self.engine.connect() as connection:
            rows = connection.execute(text("""SELECT s.*, a.original_input, a.result_json, a.status
                FROM sessions s JOIN analyses a ON a.id=(SELECT id FROM analyses WHERE session_id=s.session_id
                ORDER BY turn_index DESC LIMIT 1)
                ORDER BY s.pinned DESC, s.archived ASC, s.updated_at DESC LIMIT :limit"""), {"limit": limit}).all()
        return [dict(row._mapping) for row in rows]

    def set_session_title(self, session_id: str, title: str) -> bool:
        with self.engine.begin() as connection:
            changed = connection.execute(text("""UPDATE sessions SET title=:title
                WHERE session_id=:session AND (title IS NULL OR title='')"""),
                {"session": session_id, "title": title})
        return changed.rowcount == 1

    def get_session(self, session_id: str):
        with self.engine.connect() as connection:
            row = connection.execute(text(
                "SELECT * FROM sessions WHERE session_id=:session"), {"session": session_id}).first()
        return dict(row._mapping) if row else None

    def first_session_record(self, session_id: str):
        with self.engine.connect() as connection:
            row = connection.execute(text("""SELECT * FROM analyses
                WHERE session_id=:session ORDER BY turn_index ASC LIMIT 1"""),
                {"session": session_id}).first()
        return self._decode(row)

    def update_session(self, session_id: str, *, pinned: bool | None = None, archived: bool | None = None):
        values = {"session": session_id, "pinned": None if pinned is None else int(pinned),
                  "archived": None if archived is None else int(archived)}
        with self.engine.begin() as connection:
            changed = connection.execute(text("""UPDATE sessions SET
                pinned=COALESCE(:pinned,pinned), archived=COALESCE(:archived,archived),
                updated_at=:updated WHERE session_id=:session"""),
                {**values, "updated": datetime.now(timezone.utc).isoformat()})
            if changed.rowcount != 1:
                return None
            row = connection.execute(text("SELECT * FROM sessions WHERE session_id=:session"), values).first()
        return dict(row._mapping)

    def delete_session(self, session_id: str) -> bool:
        with self.engine.begin() as connection:
            changed = connection.execute(text("DELETE FROM sessions WHERE session_id=:session"), {"session": session_id})
            connection.execute(text("DELETE FROM analyses WHERE session_id=:session"), {"session": session_id})
        return changed.rowcount == 1

    def session_records(self, session_id: str, *, limit: int = 100, completed_only=False):
        condition = " AND status='completed'" if completed_only else ""
        with self.engine.connect() as connection:
            rows = connection.execute(text(
                "SELECT * FROM analyses WHERE session_id=:session" + condition +
                " ORDER BY turn_index DESC LIMIT :limit"
            ), {"session": session_id, "limit": limit}).all()
        return [self._decode(row) for row in reversed(rows)]

    def session_page(self, session_id: str, *, before: int | None = None, limit: int = 100):
        with self.engine.connect() as connection:
            rows = connection.execute(text(
                "SELECT * FROM analyses WHERE session_id=:session AND (:before IS NULL OR turn_index<:before)"
                " ORDER BY turn_index DESC LIMIT :limit"
            ), {"session": session_id, "before": before, "limit": limit+1}).all()
        more = len(rows) > limit
        records = [self._decode(row) for row in reversed(rows[:limit])]
        return records, records[0]["turn_index"] if more else None

    def close(self):
        self.engine.dispose()
