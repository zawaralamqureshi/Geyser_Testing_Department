"""
Add date_ymd, group_id, test_date columns to existing test_runs table.
Run: python -m scripts.migrate_add_date_group [--project PROJECT] [--dataset DATASET] [--key PATH]

Use this if you created test_runs before these columns were added to the schema.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from google.cloud import bigquery


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="geyser-testing-department", help="GCP project ID")
    ap.add_argument("--dataset", default="electrical_tests", help="BigQuery dataset name")
    ap.add_argument("--key", type=Path, help="Path to service account JSON key")
    args = ap.parse_args()

    from google.oauth2 import service_account

    creds = None
    if args.key and args.key.exists():
        creds = service_account.Credentials.from_service_account_file(str(args.key))

    client = bigquery.Client(project=args.project, credentials=creds)

    table_id = f"{args.project}.{args.dataset}.test_runs"
    columns_to_add = [
        ("date_ymd", "STRING"),
        ("group_id", "STRING"),
        ("test_date", "DATE"),
    ]

    for col_name, col_type in columns_to_add:
        try:
            sql = f"ALTER TABLE `{table_id}` ADD COLUMN IF NOT EXISTS {col_name} {col_type}"
            client.query(sql).result()
            print(f"Added column {col_name} to {table_id}")
        except Exception as e:
            print(f"Column {col_name}: {e}")

    # Backfill from index_id for existing rows
    print("Backfilling date_ymd, group_id, test_date from index_id...")
    try:
        backfill_sql = f"""
        UPDATE `{table_id}` t
        SET
          date_ymd = REGEXP_EXTRACT(t.index_id, r'^(\\d{{6}})'),
          group_id = REGEXP_EXTRACT(t.index_id, r'_([A-Za-z]+\\d*)_'),
          test_date = SAFE.PARSE_DATE('%y%m%d', REGEXP_EXTRACT(t.index_id, r'^(\\d{{6}})'))
        WHERE t.date_ymd IS NULL AND t.index_id IS NOT NULL
        """
        client.query(backfill_sql).result()
        print("Backfill complete.")
    except Exception as e:
        print(f"Backfill: {e}")


if __name__ == "__main__":
    main()
