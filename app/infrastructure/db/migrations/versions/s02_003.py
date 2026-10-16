"""空间创建防重；兼容早期版本中未记录创建键的行。"""
from alembic import op
import sqlalchemy as sa

revision = "s02_003"
down_revision = "s02_002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("workspaces", sa.Column("creation_key", sa.String(128), nullable=True))
    op.add_column("workspaces", sa.Column("creation_hash", sa.String(64), nullable=True))
    op.create_index("ix_workspaces_creation_key", "workspaces", ["owner_id", "creation_key"], unique=True)


def downgrade():
    op.drop_index("ix_workspaces_creation_key", table_name="workspaces")
    op.drop_column("workspaces", "creation_hash")
    op.drop_column("workspaces", "creation_key")
