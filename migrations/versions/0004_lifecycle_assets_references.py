"""Add subject-owned assets and reviewed reference source versions."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from packages_py.config import Settings

revision = "0004_lifecycle_assets_references"
down_revision = "0003_tool_payload_hash"
branch_labels = None
depends_on = None

def upgrade():
    schema = Settings().database_schema
    op.create_table(
        "assets",
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("object_key", sa.String(512), nullable=False),
        sa.Column("modality", sa.String(16), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("input_version_id", postgresql.UUID(as_uuid=True)),
        sa.Column("result_version_id", postgresql.UUID(as_uuid=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        schema=schema,
    )
    op.create_table(
        "sources",
        sa.Column("source_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source_type", sa.String(64), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        schema=schema,
    )
    op.create_table(
        "source_versions",
        sa.Column("source_version_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("full_text", sa.Text, nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("license_status", sa.String(32), nullable=False),
        sa.Column("review_status", sa.String(32), nullable=False),
        sa.Column("locator", sa.String(512)),
        schema=schema,
    )

def downgrade():
    schema = Settings().database_schema
    op.drop_table("source_versions", schema=schema)
    op.drop_table("sources", schema=schema)
    op.drop_table("assets", schema=schema)
