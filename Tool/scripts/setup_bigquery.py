"""
Create BigQuery dataset and tables for electrical test data.
Run: python -m scripts.setup_bigquery [--project PROJECT] [--dataset DATASET]
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

    # Create dataset
    dataset_id = f"{args.project}.{args.dataset}"
    dataset = bigquery.Dataset(dataset_id)
    dataset.location = "EU"
    try:
        client.create_dataset(dataset, exists_ok=True)
        print(f"Dataset {dataset_id} ready.")
    except Exception as e:
        print(f"Dataset: {e}")

    # test_runs schema
    test_runs_schema = [
        bigquery.SchemaField("run_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("index_id", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("object_id", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("entity_type", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("protocol_detected", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("program_text", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("analyzer_model", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("channel", "INT64", mode="NULLABLE"),
        bigquery.SchemaField("sample_rate_hz", "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("test_started", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("date_ymd", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("group_id", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("test_date", "DATE", mode="NULLABLE"),
        bigquery.SchemaField("source_path", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("processed_at", "STRING", mode="NULLABLE"),
    ]

    # cycle_metrics schema
    cycle_metrics_schema = [
        bigquery.SchemaField("run_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("index_id", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("cycle_no", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("duration_s", "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("voltage_end_v", "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("current_end_a", "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("temperature_c", "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("esr_periodic_ohm", "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("capacity_ah", "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("energy_wh", "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("capacity_charge_ah", "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("capacity_discharge_ah", "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("energy_charge_wh", "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("energy_discharge_wh", "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("capacitance_f", "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("esr_charge_ohm", "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("esr_discharge_ohm", "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("leakage_current_a", "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("coulombic_eff_pct", "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("energy_eff_pct", "FLOAT64", mode="NULLABLE"),
    ]

    # step_metrics schema
    step_metrics_schema = [
        bigquery.SchemaField("run_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("index_id", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("cycle_no", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("step_no", "INT64", mode="NULLABLE"),
        bigquery.SchemaField("step_tag", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("step_type", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("duration_s", "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("voltage_end_v", "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("current_end_a", "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("temperature_c", "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("esr_periodic_ohm", "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("capacity_ah", "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("energy_wh", "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("capacitance_f", "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("esr_charge_ohm", "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("esr_discharge_ohm", "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("leakage_current_a", "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("coulombic_eff_pct", "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("energy_eff_pct", "FLOAT64", mode="NULLABLE"),
    ]

    # time_series schema (RAW time-series: time, I, V, T, Ah, Wh per sample)
    time_series_schema = [
        bigquery.SchemaField("run_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("index_id", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("cycle_no", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("step_no", "INT64", mode="NULLABLE"),
        bigquery.SchemaField("step_type", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("seq", "INT64", mode="NULLABLE"),
        bigquery.SchemaField("time_s", "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("voltage_v", "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("current_a", "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("temperature_c", "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("capacity_ah", "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("energy_wh", "FLOAT64", mode="NULLABLE"),
    ]

    # esr_computed schema (recomputed ESR from RAW at 10ms, 1s)
    esr_computed_schema = [
        bigquery.SchemaField("run_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("index_id", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("cycle_no", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("step_no", "INT64", mode="NULLABLE"),
        bigquery.SchemaField("direction", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("delay_s", "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("esr_ohm", "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("v_end_v", "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("v_at_delay_v", "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("current_a", "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("sample_rate_hz", "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("is_approximate", "BOOL", mode="NULLABLE"),
        bigquery.SchemaField("reason", "STRING", mode="NULLABLE"),
    ]

    # curves schema (downsampled for Looker: CYCLING, CV, dQ/dV)
    curves_schema = [
        bigquery.SchemaField("run_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("index_id", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("cycle_no", "INT64", mode="NULLABLE"),
        bigquery.SchemaField("step_no", "INT64", mode="NULLABLE"),
        bigquery.SchemaField("curve_type", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("seq", "INT64", mode="NULLABLE"),
        bigquery.SchemaField("x", "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("y", "FLOAT64", mode="NULLABLE"),
        bigquery.SchemaField("y2", "FLOAT64", mode="NULLABLE"),
    ]

    # run_fingerprints schema (per-table fingerprint for smart skip)
    run_fingerprints_schema = [
        bigquery.SchemaField("run_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("table_name", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("fingerprint", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("updated_at", "TIMESTAMP", mode="NULLABLE"),
    ]

    for name, schema in [
        ("test_runs", test_runs_schema),
        ("cycle_metrics", cycle_metrics_schema),
        ("step_metrics", step_metrics_schema),
        ("time_series", time_series_schema),
        ("esr_computed", esr_computed_schema),
        ("curves", curves_schema),
        ("run_fingerprints", run_fingerprints_schema),
    ]:
        table_id = f"{dataset_id}.{name}"
        table = bigquery.Table(table_id, schema=schema)
        try:
            client.create_table(table, exists_ok=True)
            print(f"Table {table_id} ready.")
        except Exception as e:
            print(f"Table {name}: {e}")


if __name__ == "__main__":
    main()
