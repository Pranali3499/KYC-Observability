"""
data_ingestion.py
Demo Piece 1 -- Dataset Acquisition & PostgreSQL Storage

Loads the BAF (Bank Account Fraud) Base CSV into a Dockerized
PostgreSQL 17 instance, table: kyc_transactions.

Usage:
    python data_ingestion.py --csv path/to/Base.csv
"""

import argparse
import sys
import pandas as pd
from sqlalchemy import text
from db_config import get_engine
from provenance import log_provenance

TABLE_NAME = "kyc_transactions"
# PostgreSQL allows a max of 65,535 bind parameters per statement.
# With method="multi", pandas packs (columns x chunksize) parameters into
# one INSERT -- at 33 columns, chunksize must stay well under 65535/33 (~1985)
# to avoid PendingRollbackError from an aborted oversized statement.
CHUNK_SIZE = 1_000


def load_csv(csv_path: str) -> pd.DataFrame:
    print(f"Reading CSV from {csv_path} ...")
    df = pd.read_csv(csv_path)
    print(f"Loaded {len(df):,} rows, {df.shape[1]} columns")
    return df


def write_to_postgres(df: pd.DataFrame):
    engine = get_engine()
    print(f"Connecting to PostgreSQL 17 ... target table: {TABLE_NAME}")

    # if_exists="replace" on first run keeps re-runs idempotent for a demo;
    # switch to "append" once you're doing incremental loads.
    # method="multi" removed: at 33 columns, even a modest chunksize risks
    # exceeding PostgreSQL's 65535 bind-parameter-per-statement limit.
    # Default method (one INSERT per row via executemany) is slower but
    # reliable, and psycopg2 batches it reasonably efficiently under the hood.
    try:
        df.to_sql(
            TABLE_NAME,
            engine,
            if_exists="replace",
            index=False,
            chunksize=CHUNK_SIZE,
        )
    except Exception:
        # Ensure no connection is left in a broken transaction state,
        # which is what caused the PendingRollbackError on the retry.
        engine.dispose()
        raise
    print("Write complete.")

    with engine.connect() as conn:
        # De-dup safeguard: add a primary/unique key if the dataset doesn't have one
        try:
            conn.execute(text(f'ALTER TABLE {TABLE_NAME} ADD COLUMN IF NOT EXISTS row_id SERIAL PRIMARY KEY;'))
            conn.commit()
        except Exception as e:
            conn.rollback()  # reset the connection so it can run further statements
            print(f"(non-fatal) could not add row_id PK: {e}")

        result = conn.execute(text(f"SELECT COUNT(*) FROM {TABLE_NAME}"))
        row_count = result.scalar()

    print("Dataset verification:")
    print(f"  Rows loaded : {row_count:,}")
    print(f"  Columns     : {df.shape[1]}")
    print(f"  Table       : {TABLE_NAME}")
    return row_count, engine


def main():
    parser = argparse.ArgumentParser(description="Demo Piece 1: Ingest BAF dataset into PostgreSQL")
    parser.add_argument("--csv", required=True, help="Path to BAF Base.csv")
    args = parser.parse_args()

    print("=" * 65)
    print("DEMO PIECE 1 -- Dataset Acquisition & PostgreSQL Storage")
    print("=" * 65)

    df = load_csv(args.csv)
    row_count, engine = write_to_postgres(df)

    if row_count > 0:
        # Stage 1 -- provenance metadata: records which source file,
        # which git commit, and how many rows -- for auditability.
        log_provenance(
            engine,
            script_name="data_ingestion.py",
            source_dataset=args.csv,
            target_table=TABLE_NAME,
            row_count=row_count,
        )
        print("[demo1] PASS -- Dataset successfully stored in PostgreSQL.")
    else:
        print("[demo1] FAIL -- No rows found after write.")
        sys.exit(1)


if __name__ == "__main__":
    main()
