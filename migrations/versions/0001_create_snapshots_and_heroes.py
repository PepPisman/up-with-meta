from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE snapshots (
            snapshot_at      TIMESTAMPTZ PRIMARY KEY,
            patch            TEXT NOT NULL,
            source           TEXT NOT NULL,
            days_since_patch SMALLINT CHECK (days_since_patch >= 0)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE heroes (
            hero_id        SMALLINT PRIMARY KEY,
            name           TEXT NOT NULL UNIQUE,
            localized_name TEXT NOT NULL,
            primary_attr   TEXT NOT NULL CHECK (primary_attr IN ('str', 'agi', 'int', 'all')),
            attack_type    TEXT NOT NULL CHECK (attack_type IN ('Melee', 'Ranged')),
            roles          TEXT[] NOT NULL DEFAULT '{}'
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE heroes")
    op.execute("DROP TABLE snapshots")
