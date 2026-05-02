"""merge migration heads

Revision ID: 507deda6302c
Revises: 20260501_1300, 20260502_1400
Create Date: 2026-05-01 22:38:45.750030

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '507deda6302c'
down_revision = ('20260501_1300', '20260502_1400')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
