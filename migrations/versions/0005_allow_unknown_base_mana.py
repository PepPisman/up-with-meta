from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE hero_base_stats ALTER COLUMN base_mana DROP NOT NULL")


def downgrade() -> None:
    op.execute("DELETE FROM hero_base_stats WHERE base_mana IS NULL")
    op.execute("ALTER TABLE hero_base_stats ALTER COLUMN base_mana SET NOT NULL")
