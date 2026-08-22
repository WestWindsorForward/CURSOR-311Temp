"""screen resident photos when they are picked, not when the form is submitted

Two additive changes, no data touched.

`screened_photos` holds a photo that has already been moderated and blurred,
waiting for the report it belongs to. The resident portal now uploads and
screens a photo the moment it is chosen -- while the resident is still typing
the description -- so the Vision round trip stops sitting on the Submit button.
The row holds the REDACTED bytes only, and is deleted when the handle is spent
or reaped an hour after it was minted.

`service_requests.media_pending_review` holds a photo the redactor could not
clear. That case used to publish the original unredacted with a note in a log,
which is the wrong direction to fail on the one axis the redactor exists to
protect; it now waits here for a staff member to look at it. NULL on every
existing row, which reads as "nothing waiting" -- the correct answer for
anything filed before this.

Revision ID: b8c4d2e6f0a1
Revises: e5a3c7b9d1f4
Create Date: 2026-08-12 09:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b8c4d2e6f0a1'
down_revision: Union[str, None] = 'a8c6e2f4b9d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'screened_photos',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('token', sa.String(length=64), nullable=False),
        sa.Column('verdict', sa.String(length=20), nullable=False,
                  server_default='ready'),
        sa.Column('reason', sa.String(length=64), server_default=''),
        sa.Column('media', sa.Text(), nullable=True),
        sa.Column('faces', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('plates', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_screened_photos_token', 'screened_photos', ['token'],
                    unique=True)
    # The reaper sweeps by expiry every hour and this table churns, so the scan
    # wants an index rather than a sequential read over whatever is in flight.
    op.create_index('ix_screened_photos_expires_at', 'screened_photos',
                    ['expires_at'])

    op.add_column(
        'service_requests',
        sa.Column('media_pending_review', sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('service_requests', 'media_pending_review')
    op.drop_index('ix_screened_photos_expires_at', table_name='screened_photos')
    op.drop_index('ix_screened_photos_token', table_name='screened_photos')
    op.drop_table('screened_photos')
