"""let an operator answer the registration prompt for the whole deployment

The "Register your deployment" prompt is dismissed per browser, on purpose: it
is a nudge rather than a task, and there was no server-side state to key it to.
That is right for a town, where the second admin to sign in has no reason to be
asked again about something the first dealt with.

It is wrong for an operator running demo instances, where every visitor in every
fresh browser meets a prompt whose question was settled long ago. So there is
now something to key it to. `registration_prompt_dismissed` is the operator
answering for the deployment -- not a replacement for the per-browser flags,
which keep working exactly as they did, but an override on top of them.

Additive and defaulted false, so an install that has not been switched on
behaves precisely as before.

Revision ID: f6b4d8e2a3c5
Revises: e5a3c7b9d1f4
Create Date: 2026-08-17 09:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f6b4d8e2a3c5'
down_revision: Union[str, None] = 'e5a3c7b9d1f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'system_settings',
        sa.Column(
            'registration_prompt_dismissed',
            sa.Boolean(),
            nullable=False,
            server_default='false',
        ),
    )


def downgrade() -> None:
    op.drop_column('system_settings', 'registration_prompt_dismissed')
