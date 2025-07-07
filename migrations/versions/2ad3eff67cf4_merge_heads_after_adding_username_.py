"""Merge heads after adding username/password

Revision ID: 2ad3eff67cf4
Revises: 1d4dee73c43b, 28b255140399
Create Date: 2025-07-04 13:29:47.722516

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '2ad3eff67cf4'
down_revision = ('1d4dee73c43b', '28b255140399')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
