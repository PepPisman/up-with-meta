from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

LATEST_VIEWS = (
    "v_latest_bracket_winrate",
    "v_latest_bucket_winrate",
    "v_latest_winrate_by_mode",
)

BRACKET_PICKS = " + ".join(f"w.b{n}_pick" for n in range(1, 8))
BRACKET_WINS = " + ".join(f"w.b{n}_win" for n in range(1, 8))

CREATE_BRACKET = """
    CREATE VIEW v_latest_bracket_winrate AS
    SELECT
        l.snapshot_at, l.patch, l.source, l.days_since_patch, w.hero_id,
        v.bracket, v.picks, v.wins,
        v.wins::float / NULLIF(v.picks, 0) AS winrate,
        l.is_current
    FROM v_latest_winrate_snapshot l
    JOIN hero_winrate_stats w ON w.snapshot_at = l.snapshot_at
    CROSS JOIN LATERAL (VALUES
        (1, w.b1_pick, w.b1_win), (2, w.b2_pick, w.b2_win), (3, w.b3_pick, w.b3_win),
        (4, w.b4_pick, w.b4_win), (5, w.b5_pick, w.b5_win), (6, w.b6_pick, w.b6_win),
        (7, w.b7_pick, w.b7_win)
    ) AS v (bracket, picks, wins)
"""

CREATE_BUCKET = """
    CREATE VIEW v_latest_bucket_winrate AS
    SELECT
        l.snapshot_at, l.patch, l.source, l.days_since_patch, w.hero_id,
        v.bucket, v.picks, v.wins,
        v.wins::float / NULLIF(v.picks, 0) AS winrate,
        l.is_current
    FROM v_latest_winrate_snapshot l
    JOIN hero_winrate_stats w ON w.snapshot_at = l.snapshot_at
    CROSS JOIN LATERAL (VALUES
        ('low',  (w.b1_pick + w.b2_pick + w.b3_pick)::bigint, (w.b1_win + w.b2_win + w.b3_win)::bigint),
        ('mid',  (w.b4_pick + w.b5_pick)::bigint,             (w.b4_win + w.b5_win)::bigint),
        ('high', (w.b6_pick + w.b7_pick)::bigint,             (w.b6_win + w.b7_win)::bigint)
    ) AS v (bucket, picks, wins)
"""

CREATE_MODE = f"""
    CREATE VIEW v_latest_winrate_by_mode AS
    SELECT
        l.snapshot_at, l.patch, l.source, l.days_since_patch, w.hero_id,
        v.mode, v.picks, v.wins,
        v.wins::float / NULLIF(v.picks, 0) AS winrate,
        l.is_current
    FROM v_latest_winrate_snapshot l
    JOIN hero_winrate_stats w ON w.snapshot_at = l.snapshot_at
    CROSS JOIN LATERAL (VALUES
        ('ranked', ({BRACKET_PICKS})::bigint, ({BRACKET_WINS})::bigint),
        ('pub',    w.pub_pick::bigint,        w.pub_win::bigint),
        ('turbo',  w.turbo_pick::bigint,      w.turbo_win::bigint),
        ('pro',    w.pro_pick::bigint,        w.pro_win::bigint)
    ) AS v (mode, picks, wins)
"""

PREVIOUS_VIEWS = (
    ("v_latest_bracket_winrate", "v_bracket_winrate"),
    ("v_latest_bucket_winrate", "v_bucket_winrate"),
    ("v_latest_winrate_by_mode", "v_winrate_by_mode"),
)


def upgrade() -> None:
    for name in LATEST_VIEWS:
        op.execute(f"DROP VIEW {name}")
    op.execute(CREATE_BRACKET)
    op.execute(CREATE_BUCKET)
    op.execute(CREATE_MODE)


def downgrade() -> None:
    for name in LATEST_VIEWS:
        op.execute(f"DROP VIEW {name}")
    for latest, base in PREVIOUS_VIEWS:
        op.execute(
            f"""
            CREATE VIEW {latest} AS
            SELECT b.*, l.is_current
            FROM {base} b
            JOIN v_latest_winrate_snapshot l USING (snapshot_at)
            """
        )
