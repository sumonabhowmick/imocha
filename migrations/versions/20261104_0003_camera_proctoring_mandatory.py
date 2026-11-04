"""camera proctoring mandatory

Every assessment now requires camera proctoring. Existing assessments are switched to
"required" (this only affects attempts started from now on; past attempts, results and
proctoring events are unchanged) and new rows default to "required".

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-04
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE assessments SET proctoring_mode = 'required' WHERE proctoring_mode <> 'required'")
    op.alter_column("assessments", "proctoring_mode", server_default="required")


def downgrade() -> None:
    # The previous per-assessment choices are not recorded, so they cannot be restored;
    # only the column default goes back to "off".
    op.alter_column("assessments", "proctoring_mode", server_default="off")
