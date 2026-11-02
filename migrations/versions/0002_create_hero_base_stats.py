from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE hero_base_stats (
            snapshot_at       TIMESTAMPTZ NOT NULL REFERENCES snapshots (snapshot_at),
            hero_id           SMALLINT NOT NULL REFERENCES heroes (hero_id),
            base_str          SMALLINT NOT NULL,
            base_agi          SMALLINT NOT NULL,
            base_int          SMALLINT NOT NULL,
            str_gain          REAL NOT NULL CHECK (str_gain BETWEEN 0 AND 6),
            agi_gain          REAL NOT NULL CHECK (agi_gain BETWEEN 0 AND 6),
            int_gain          REAL NOT NULL CHECK (int_gain BETWEEN 0 AND 6),
            base_attack_min   SMALLINT NOT NULL,
            base_attack_max   SMALLINT NOT NULL,
            base_armor        REAL NOT NULL,
            base_health_regen REAL NOT NULL,
            base_mana_regen   REAL NOT NULL,
            base_mana         SMALLINT NOT NULL,
            attack_range      SMALLINT NOT NULL,
            attack_rate       REAL NOT NULL,
            attack_point      REAL NOT NULL,
            move_speed        SMALLINT NOT NULL,
            turn_rate         REAL,
            day_vision        SMALLINT NOT NULL,
            night_vision      SMALLINT NOT NULL,
            PRIMARY KEY (snapshot_at, hero_id),
            CHECK (base_attack_min <= base_attack_max),
            CHECK (attack_range > 0),
            CHECK (move_speed BETWEEN 100 AND 700)
        )
        """
    )
    op.execute("CREATE INDEX idx_hero_base_stats_hero_id ON hero_base_stats (hero_id)")


def downgrade() -> None:
    op.execute("DROP TABLE hero_base_stats")
