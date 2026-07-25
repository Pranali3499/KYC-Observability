import pandas as pd
from sqlalchemy import create_engine
import time

csv_path = r"D:\kyc-observability\Data\Base.csv"

print("Reading CSV...")
start = time.time()
df = pd.read_csv(csv_path)
print(f"Loaded {len(df)} rows, {len(df.columns)} columns in {time.time()-start:.1f}s")
print("Columns:", df.columns.tolist())

engine = create_engine("postgresql://postgres:postgres@localhost:5432/kyc_db")

print("Writing to PostgreSQL (this may take a few minutes)...")
start = time.time()
df.to_sql(
    "kyc_transactions",
    engine,
    if_exists="append",
    index=False,
    method="multi",
    chunksize=5000
)
print(f"Done in {time.time()-start:.1f}s")