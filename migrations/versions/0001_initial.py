"""Initial business tables; checkpoint storage has its own schema."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from packages_py.config import Settings

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    settings = Settings()
    app = settings.database_schema
    op.execute(sa.text(f'CREATE SCHEMA IF NOT EXISTS "{app}"'))
    op.execute(sa.text(f'CREATE SCHEMA IF NOT EXISTS "{settings.checkpoint_schema}"'))
    op.create_table(
        "subjects",
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        schema=app,
    )
    op.create_table(
        "access_tokens",
        sa.Column("token_digest", sa.String(64), primary_key=True),
        sa.Column(
            "subject_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{app}.subjects.subject_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        schema=app,
    )
    op.create_table(
        "analysis_sessions",
        sa.Column("session_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "subject_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{app}.subjects.subject_id"),
            nullable=False,
        ),
        sa.Column("input_version_id", postgresql.UUID(as_uuid=True)),
        sa.Column("active_run_id", postgresql.UUID(as_uuid=True)),
        sa.Column(
            "allowed_source_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        schema=app,
    )
    op.create_table(
        "input_versions",
        sa.Column("input_version_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{app}.analysis_sessions.session_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema=app,
    )
    op.create_table(
        "runs",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{app}.analysis_sessions.session_id"),
            nullable=False,
        ),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("record", postgresql.JSONB, nullable=False),
        sa.Column("config_snapshot", postgresql.JSONB, nullable=False),
        sa.Column("config_snapshot_schema_version", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("session_id", "request_id", name="uq_runs_request"),
        schema=app,
    )
    op.create_table(
        "result_versions",
        sa.Column("result_version_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{app}.runs.run_id"),
            nullable=False,
        ),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("input_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("validated_at", sa.DateTime(timezone=True)),
        schema=app,
    )
    op.create_table(
        "consents",
        sa.Column("consent_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        schema=app,
    )
    op.create_table(
        "improvement_copies",
        sa.Column("copy_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("consent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("data_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        schema=app,
    )
    op.create_table(
        "deletion_jobs",
        sa.Column("deletion_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("pending_scopes", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("subject_id", "session_id", name="uq_deletion_session"),
        schema=app,
    )
    op.create_table(
        "deletion_attempts",
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("deletion_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("attempted_scopes", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("remaining_scopes", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("error_code", sa.String(64)),
        schema=app,
    )
    op.create_table(
        "idempotency_records",
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), nullable=False),
        schema=app,
    )
    op.create_table(
        "thread_mappings",
        sa.Column("thread_id", sa.String(128), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        schema=app,
    )
    op.create_table(
        "tool_idempotency",
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("output", postgresql.JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema=app,
    )


def downgrade():
    settings = Settings()
    op.execute(f'DROP SCHEMA IF EXISTS "{settings.checkpoint_schema}" CASCADE')
    for table in (
        "tool_idempotency",
        "thread_mappings",
        "idempotency_records",
        "deletion_attempts",
        "deletion_jobs",
        "improvement_copies",
        "consents",
        "result_versions",
        "runs",
        "input_versions",
        "analysis_sessions",
        "access_tokens",
        "subjects",
    ):
        op.drop_table(table, schema=settings.database_schema)
