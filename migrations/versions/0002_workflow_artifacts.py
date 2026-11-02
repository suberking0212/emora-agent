"""Store transient model artifacts outside LangGraph checkpoint state."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from packages_py.config import Settings

revision = "0002_workflow_artifacts"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    schema = Settings().database_schema
    op.create_table(
        "workflow_artifacts",
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema=schema,
    )


def downgrade():
    op.drop_table("workflow_artifacts", schema=Settings().database_schema)
