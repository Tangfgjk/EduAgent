"""Durable classroom input receipts, independent of the latest playback position."""
from alembic import op
import sqlalchemy as sa
revision='r14_003'
down_revision='r14_002'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('playback_receipts',sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('account_id',sa.String(36),sa.ForeignKey('accounts.id'),nullable=False),
        sa.Column('resource_id',sa.String(36),sa.ForeignKey('resource_artifacts.id'),nullable=False),
        sa.Column('creation_key',sa.String(128),nullable=False),sa.Column('request_hash',sa.String(64),nullable=False),
        sa.Column('response',sa.JSON,nullable=False),sa.Column('created_at',sa.BigInteger,nullable=False),
        sa.UniqueConstraint('account_id','resource_id','creation_key'))
    op.create_index('ix_playback_receipts_account_id','playback_receipts',['account_id'])
    if op.get_bind().dialect.name=='sqlite':
        for action in ('UPDATE','DELETE'):
            op.execute(f"CREATE TRIGGER playback_receipts_no_{action.lower()} BEFORE {action} ON playback_receipts BEGIN SELECT RAISE(ABORT, 'immutable playback receipt'); END")
    else:
        op.execute('CREATE TRIGGER playback_receipts_immutable BEFORE UPDATE OR DELETE ON playback_receipts FOR EACH ROW EXECUTE FUNCTION reject_fact_mutation()')


def downgrade():raise RuntimeError('Forward-only migration; restore a verified backup')
