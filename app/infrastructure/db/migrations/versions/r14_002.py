"""Separate one-course media currency reservations. Disabled unless approved."""
from alembic import op
import sqlalchemy as sa
revision='r14_002'
down_revision='r14_001'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('media_budgets',sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('workspace_id',sa.String(36),sa.ForeignKey('workspaces.id'),nullable=False),
        sa.Column('resource_id',sa.String(36),sa.ForeignKey('resource_artifacts.id'),nullable=False),
        sa.Column('enabled',sa.Boolean,nullable=False),sa.Column('revision',sa.Integer,nullable=False),
        sa.Column('body',sa.JSON,nullable=False),sa.Column('created_at',sa.BigInteger,nullable=False))
    op.create_index('ix_media_budgets_workspace_id','media_budgets',['workspace_id'])
    op.create_table('media_operations',sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('budget_id',sa.String(36),sa.ForeignKey('media_budgets.id'),nullable=False),sa.Column('slot',sa.String(20),nullable=False),
        sa.Column('request',sa.JSON,nullable=False),sa.Column('request_hash',sa.String(64),nullable=False),sa.Column('reserved_microyuan',sa.Integer,nullable=False),
        sa.Column('status',sa.String(32),nullable=False),sa.Column('remote_id',sa.String(200)),sa.Column('receipt',sa.JSON),
        sa.Column('lease_token',sa.String(36)),sa.Column('lease_until',sa.BigInteger),sa.Column('next_poll_at',sa.BigInteger,nullable=False),
        sa.Column('polls',sa.Integer,nullable=False),sa.Column('created_at',sa.BigInteger,nullable=False),sa.UniqueConstraint('budget_id','slot'))
    op.create_index('ix_media_operations_budget_id','media_operations',['budget_id'])
    op.create_index('ix_media_operations_status','media_operations',['status'])
    op.create_table('media_assets',sa.Column('id',sa.String(36),sa.ForeignKey('media_operations.id'),primary_key=True),
        sa.Column('body',sa.JSON,nullable=False),sa.Column('sha256',sa.String(64),nullable=False))


def downgrade():raise RuntimeError('Forward-only migration; restore a verified backup')
