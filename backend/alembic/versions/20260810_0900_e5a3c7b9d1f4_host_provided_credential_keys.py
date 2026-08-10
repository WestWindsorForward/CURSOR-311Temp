"""record which credential keys the host supplied

A hosted town can be handed working credentials by whoever runs its instance,
so the setup page does not dead-end on a key the town has no account for. That
only stays safe if ownership is written down: the host may replace or withdraw
the keys it provided and nothing else, and a town admin who types their own
value into the same box takes the key back for good.

`host_provided_keys` is that record -- key NAMES only, never values, which stay
in the secret store and the encrypted `system_secrets` copy exactly as any
town-entered credential does.

Revision ID: e5a3c7b9d1f4
Revises: d4f2a6b8c1e3
Create Date: 2026-08-10 09:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e5a3c7b9d1f4'
down_revision: Union[str, None] = 'd4f2a6b8c1e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'system_settings',
        sa.Column('host_provided_keys', sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('system_settings', 'host_provided_keys')
