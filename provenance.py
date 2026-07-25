"""
provenance.py
Stage 1 -- Provenance Metadata

Reusable module for logging pipeline run provenance to PostgreSQL.
Called from data_ingestion.py (Demo Piece 1) and feature_engineering.py
(Demo Piece 2) after each writes its output table.

Pipeline version is taken from the current git commit hash -- this
means every row written to data_provenance can be traced back to the
exact code that produced it, which is what makes this auditable.
If the working tree has uncommitted changes at run time, that's
flagged too (git_dirty=True), since a dirty tree means the recorded
commit hash doesn't fully describe what actually ran.

Usage (from another script):
    from provenance import log_provenance
    log_provenance(
        engine,
        script_name="data_ingestion.py",
        source_dataset="BAF Base.csv",
        target_table="kyc_transactions",
        row_count=row_count,
    )
"""

import datetime as dt
import subprocess

from sqlalchemy import text

TABLE_NAME = "data_provenance"

CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    id SERIAL PRIMARY KEY,
    run_timestamp TIMESTAMP NOT NULL DEFAULT NOW(),
    script_name TEXT NOT NULL,
    source_dataset TEXT NOT NULL,
    target_table TEXT NOT NULL,
    row_count BIGINT,
    pipeline_version TEXT NOT NULL,
    git_dirty BOOLEAN NOT NULL,
    notes TEXT
);
"""


def _run_git(args: list[str]) -> str | None:
    """Run a git command in the current working directory. Returns None
    if git isn't available or this isn't a git repo (script still runs,
    just falls back to an 'unversioned' tag)."""
    try:
        result = subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def get_pipeline_version() -> tuple[str, bool]:
    """
    Returns (version_string, is_dirty).

    version_string is the short git commit hash (e.g. 'a1b2c3d'), or
    'unversioned-<timestamp>' if this isn't a git repo yet.
    is_dirty is True if there are uncommitted changes -- meaning the
    commit hash alone doesn't fully capture what code actually ran.
    """
    commit_hash = _run_git(["rev-parse", "--short", "HEAD"])

    if commit_hash is None:
        # No git repo yet (or git not installed) -- fall back to a
        # timestamp tag so runs are still distinguishable, and this
        # makes it obvious in the data that versioning wasn't wired
        # up yet for that run.
        fallback = f"unversioned-{dt.datetime.now():%Y%m%dT%H%M%S}"
        return fallback, True

    status_output = _run_git(["status", "--porcelain"])
    is_dirty = bool(status_output)  # non-empty output = uncommitted changes

    return commit_hash, is_dirty


def log_provenance(
    engine,
    script_name: str,
    source_dataset: str,
    target_table: str,
    row_count: int,
    notes: str | None = None,
) -> None:
    """Writes one provenance row. Creates the table on first use."""
    with engine.connect() as conn:
        conn.execute(text(CREATE_TABLE_SQL))
        conn.commit()

    version, dirty = get_pipeline_version()

    if dirty:
        print(
            f"  [provenance] WARNING: uncommitted git changes present. "
            f"Recorded version '{version}' may not fully describe this run -- "
            f"commit your changes before the next run for a clean audit trail."
        )

    with engine.connect() as conn:
        conn.execute(
            text(
                f"""
                INSERT INTO {TABLE_NAME}
                    (script_name, source_dataset, target_table, row_count,
                     pipeline_version, git_dirty, notes)
                VALUES
                    (:script_name, :source_dataset, :target_table, :row_count,
                     :pipeline_version, :git_dirty, :notes)
                """
            ),
            {
                "script_name": script_name,
                "source_dataset": source_dataset,
                "target_table": target_table,
                "row_count": row_count,
                "pipeline_version": version,
                "git_dirty": dirty,
                "notes": notes,
            },
        )
        conn.commit()

    print(f"  [provenance] Logged: {script_name} -> {target_table} (version {version})")
