"""
Create run_fingerprints table for per-table fingerprint (smart skip on upload).
Run: python -m scripts.migrate_add_run_fingerprints [--project PROJECT] [--dataset DATASET] [--key PATH]

Use this if you created tables before run_fingerprints was added to the schema.
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

    dataset_id = f"{args.project}.{args.dataset}"
    table_id = f"{dataset_id}.run_fingerprints"

    schema = [
        bigquery.SchemaField("run_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("table_name", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("fingerprint", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("updated_at", "TIMESTAMP", mode="NULLABLE"),
    ]

    table = bigquery.Table(table_id, schema=schema)
    try:
        client.create_table(table, exists_ok=True)
        print(f"Table {table_id} ready.")
    except Exception as e:
        print(f"Create table: {e}")


if __name__ == "__main__":
    main()
