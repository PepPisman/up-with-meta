from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE hero_winrate_stats (
            snapshot_at TIMESTAMPTZ NOT NULL REFERENCES snapshots (snapshot_at),
            hero_id     SMALLINT NOT NULL REFERENCES heroes (hero_id),
            b1_pick INTEGER NOT NULL DEFAULT 0, b1_win INTEGER NOT NULL DEFAULT 0,
            b2_pick INTEGER NOT NULL DEFAULT 0, b2_win INTEGER NOT NULL DEFAULT 0,
            b3_pick INTEGER NOT NULL DEFAULT 0, b3_win INTEGER NOT NULL DEFAULT 0,
            b4_pick INTEGER NOT NULL DEFAULT 0, b4_win INTEGER NOT NULL DEFAULT 0,
            b5_pick INTEGER NOT NULL DEFAULT 0, b5_win INTEGER NOT NULL DEFAULT 0,
            b6_pick INTEGER NOT NULL DEFAULT 0, b6_win INTEGER NOT NULL DEFAULT 0,
            b7_pick INTEGER NOT NULL DEFAULT 0, b7_win INTEGER NOT NULL DEFAULT 0,
            pub_pick   INTEGER NOT NULL DEFAULT 0, pub_win   INTEGER NOT NULL DEFAULT 0,
            turbo_pick INTEGER NOT NULL DEFAULT 0, turbo_win INTEGER NOT NULL DEFAULT 0,
            pro_pick   INTEGER NOT NULL DEFAULT 0, pro_win   INTEGER NOT NULL DEFAULT 0,
            pro_ban    INTEGER NOT NULL DEFAULT 0 CHECK (pro_ban >= 0),
            PRIMARY KEY (snapshot_at, hero_id),
            CHECK (b1_win BETWEEN 0 AND b1_pick),
            CHECK (b2_win BETWEEN 0 AND b2_pick),
            CHECK (b3_win BETWEEN 0 AND b3_pick),
            CHECK (b4_win BETWEEN 0 AND b4_pick),
            CHECK (b5_win BETWEEN 0 AND b5_pick),
            CHECK (b6_win BETWEEN 0 AND b6_pick),
            CHECK (b7_win BETWEEN 0 AND b7_pick),
            CHECK (pub_win BETWEEN 0 AND pub_pick),
            CHECK (turbo_win BETWEEN 0 AND turbo_pick),
            CHECK (pro_win BETWEEN 0 AND pro_pick)
        )
        """
    )
    op.execute("CREATE INDEX idx_hero_winrate_stats_hero_id ON hero_winrate_stats (hero_id)")


def downgrade() -> None:
    op.execute("DROP TABLE hero_winrate_stats")
