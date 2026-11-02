from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE VIEW v_bracket_winrate AS
        SELECT
            w.snapshot_at, s.patch, s.source, s.days_since_patch, w.hero_id,
            v.bracket, v.picks, v.wins,
            v.wins::float / NULLIF(v.picks, 0) AS winrate
        FROM hero_winrate_stats w
        JOIN snapshots s USING (snapshot_at)
        CROSS JOIN LATERAL (VALUES
            (1, w.b1_pick, w.b1_win), (2, w.b2_pick, w.b2_win), (3, w.b3_pick, w.b3_win),
            (4, w.b4_pick, w.b4_win), (5, w.b5_pick, w.b5_win), (6, w.b6_pick, w.b6_win),
            (7, w.b7_pick, w.b7_win)
        ) AS v (bracket, picks, wins)
        """
    )
    op.execute(
        """
        CREATE VIEW v_bucket_winrate AS
        SELECT
            snapshot_at, patch, source, days_since_patch, hero_id,
            CASE
                WHEN bracket <= 3 THEN 'low'
                WHEN bracket <= 5 THEN 'mid'
                ELSE 'high'
            END                                      AS bucket,
            SUM(picks)                               AS picks,
            SUM(wins)                                AS wins,
            SUM(wins)::float / NULLIF(SUM(picks), 0) AS winrate
        FROM v_bracket_winrate
        GROUP BY snapshot_at, patch, source, days_since_patch, hero_id, bucket
        """
    )
    op.execute(
        """
        CREATE VIEW v_winrate_by_mode AS
        SELECT m.*, m.wins::float / NULLIF(m.picks, 0) AS winrate
        FROM (
            SELECT snapshot_at, patch, source, days_since_patch, hero_id,
                   'ranked'::text AS mode, SUM(picks) AS picks, SUM(wins) AS wins
            FROM v_bracket_winrate
            GROUP BY snapshot_at, patch, source, days_since_patch, hero_id
            UNION ALL
            SELECT w.snapshot_at, s.patch, s.source, s.days_since_patch, w.hero_id,
                   'pub', w.pub_pick, w.pub_win
            FROM hero_winrate_stats w JOIN snapshots s USING (snapshot_at)
            UNION ALL
            SELECT w.snapshot_at, s.patch, s.source, s.days_since_patch, w.hero_id,
                   'turbo', w.turbo_pick, w.turbo_win
            FROM hero_winrate_stats w JOIN snapshots s USING (snapshot_at)
            UNION ALL
            SELECT w.snapshot_at, s.patch, s.source, s.days_since_patch, w.hero_id,
                   'pro', w.pro_pick, w.pro_win
            FROM hero_winrate_stats w JOIN snapshots s USING (snapshot_at)
        ) m
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW v_winrate_by_mode")
    op.execute("DROP VIEW v_bucket_winrate")
    op.execute("DROP VIEW v_bracket_winrate")
