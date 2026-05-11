"""
Add capacity_charge_ah, capacity_discharge_ah, energy_charge_wh, energy_discharge_wh
to existing cycle_metrics table.

Run: python -m scripts.migrate_add_charge_discharge [--project PROJECT] [--dataset DATASET] [--key PATH]

Use this if you created cycle_metrics before these columns were added.
Re-upload CLK data after migration to populate the new columns.
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

    table_id = f"{args.project}.{args.dataset}.cycle_metrics"
    columns_to_add = [
        ("capacity_charge_ah", "FLOAT64"),
        ("capacity_discharge_ah", "FLOAT64"),
        ("energy_charge_wh", "FLOAT64"),
        ("energy_discharge_wh", "FLOAT64"),
    ]

    for col_name, col_type in columns_to_add:
        try:
            sql = f"ALTER TABLE `{table_id}` ADD COLUMN IF NOT EXISTS {col_name} {col_type}"
            client.query(sql).result()
            print(f"Added column {col_name} to {table_id}")
        except Exception as e:
            print(f"Column {col_name}: {e}")

    print("Done. Re-upload CLK data to populate the new columns.")


if __name__ == "__main__":
    main()
