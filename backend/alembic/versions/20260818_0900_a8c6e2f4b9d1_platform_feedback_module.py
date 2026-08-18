"""the optional platform-feedback module

One table and one settings column, both for a feature that is OFF until a town
switches it on.

`platform_feedback` holds one anonymous answer per row to a single ordered
multiple-choice question about whether Pinpoint made reporting easier: which of
five options was tapped, and when. There is no user id, no session id, no IP
(hashed or otherwise), no user agent and no free-text column, so a row cannot be
attributed to a person and there is nothing resident-typed in it to appear in a
public-records request, to scrub from an export, or to moderate.

The free text a resident might want to write goes to email instead:
`system_settings.platform_feedback_email` is the address a "want to tell us
more?" link points at. Blank means no such link is rendered. That keeps the
sentence in a mailbox rather than in the town's database, which is the whole
reason the table has the shape it has.

The CHECK constraint is part of the design rather than belt and braces: the
column stores a constrained categorical value, and that has to be true of the
column, not only of the API in front of it.

Purely additive -- a new table and a nullable column. Nothing existing is
touched, so a town can run the new image before or after this applies.

Revision ID: a8c6e2f4b9d1
Revises: f6b4d8e2a3c5
Create Date: 2026-08-18 09:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a8c6e2f4b9d1'
down_revision: Union[str, None] = 'f6b4d8e2a3c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_ANSWERS = (
    'much_easier',
    'somewhat_easier',
    'no_difference',
    'somewhat_harder',
    'much_harder',
)


def upgrade() -> None:
    op.create_table(
        'platform_feedback',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('platform_experience', sa.String(length=20), nullable=False),
        sa.Column(
            'submitted_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint(
            "platform_experience IN ('" + "', '".join(_ANSWERS) + "')",
            name='ck_platform_feedback_answer',
        ),
    )
    op.create_index('ix_platform_feedback_id', 'platform_feedback', ['id'])
    op.create_index('ix_platform_feedback_submitted_at', 'platform_feedback', ['submitted_at'])
    op.add_column(
        'system_settings',
        sa.Column('platform_feedback_email', sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('system_settings', 'platform_feedback_email')
    op.drop_index('ix_platform_feedback_submitted_at', table_name='platform_feedback')
    op.drop_index('ix_platform_feedback_id', table_name='platform_feedback')
    op.drop_table('platform_feedback')
