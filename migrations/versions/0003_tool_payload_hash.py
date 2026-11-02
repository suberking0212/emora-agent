"""Bind durable tool requests to their payload."""

import sqlalchemy as sa
from alembic import op

from packages_py.config import Settings

revision = "0003_tool_payload_hash"
down_revision = "0002_workflow_artifacts"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "tool_idempotency",
        sa.Column("payload_hash", sa.String(64)),
        schema=Settings().database_schema,
    )


def downgrade():
    op.drop_column("tool_idempotency", "payload_hash", schema=Settings().database_schema)
