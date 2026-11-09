from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE VIEW v_latest_winrate_snapshot AS
        SELECT
            l.snapshot_at, l.patch, l.source, l.days_since_patch,
            l.snapshot_at = MAX(l.snapshot_at) OVER (PARTITION BY l.source) AS is_current
        FROM (
            SELECT DISTINCT ON (s.source, s.patch)
                s.snapshot_at, s.patch, s.source, s.days_since_patch
            FROM snapshots s
            WHERE EXISTS (SELECT 1 FROM hero_winrate_stats w WHERE w.snapshot_at = s.snapshot_at)
            ORDER BY s.source, s.patch, s.snapshot_at DESC
        ) l
        """
    )
    for latest, base in (
        ("v_latest_bracket_winrate", "v_bracket_winrate"),
        ("v_latest_bucket_winrate", "v_bucket_winrate"),
        ("v_latest_winrate_by_mode", "v_winrate_by_mode"),
    ):
        op.execute(
            f"""
            CREATE VIEW {latest} AS
            SELECT b.*, l.is_current
            FROM {base} b
            JOIN v_latest_winrate_snapshot l USING (snapshot_at)
            """
        )


def downgrade() -> None:
    op.execute("DROP VIEW v_latest_winrate_by_mode")
    op.execute("DROP VIEW v_latest_bucket_winrate")
    op.execute("DROP VIEW v_latest_bracket_winrate")
    op.execute("DROP VIEW v_latest_winrate_snapshot")
