from __future__ import annotations

import argparse
from pathlib import Path

from geyser_tool.config import AppConfig
from geyser_tool.pipeline import summarize_clk_files
from geyser_tool.upload import stage_only, upload_batch_to_bigquery, upload_processed_files


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Geyser cycler analysis tool (CLI)")
    parser.add_argument(
        "paths",
        nargs="*",
        help="One or more CLK files or directories to scan (required unless --upload-batch)",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Optional YAML config path",
    )
    parser.add_argument(
        "--upload",
        action="store_true",
        help="Upload scanned files to BigQuery",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --upload: process files but do not upload",
    )
    parser.add_argument(
        "--no-raw",
        action="store_true",
        help="Skip RAW file processing (CLK only, faster)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore fingerprint; process and upload all files",
    )
    parser.add_argument(
        "--force-run",
        action="append",
        default=[],
        metavar="RUN_ID",
        help="Force re-upload for specific run_id (can be repeated)",
    )
    parser.add_argument(
        "--stage-only",
        action="store_true",
        help="With --upload: process and stage to Parquet only (no GCS/BigQuery)",
    )
    parser.add_argument(
        "--upload-batch",
        type=str,
        metavar="PATH",
        help="Upload a staged batch folder to GCS and BigQuery",
    )
    args = parser.parse_args(argv)

    cfg = AppConfig.load(args.config)

    if args.upload_batch:
        batch_path = Path(args.upload_batch)
        if not batch_path.exists():
            print(f"Batch path does not exist: {batch_path}")
            return
        uploaded, err_count, err_msgs = upload_batch_to_bigquery(batch_path, config=cfg)
        print(f"Uploaded {uploaded} runs from batch.")
        for m in err_msgs[:20]:
            print(f"  Error: {m}")
        return

    if not args.paths:
        parser.error("paths required (or use --upload-batch)")

    roots = [Path(p) for p in args.paths]
    summaries = list(summarize_clk_files(roots))

    if args.upload:
        force_run_ids = set(args.force_run) if args.force_run else None
        if args.stage_only:
            batch_path, staged_count, skipped_count, err_count, err_msgs = stage_only(
                iter(summaries),
                config=cfg,
                include_raw=not args.no_raw,
                force=args.force,
                force_run_ids=force_run_ids,
            )
            total = len(summaries)
            print(f"Found {total} files. Staged {staged_count}. Skipped {skipped_count} (fingerprint match). Failed {err_count}.")
            if batch_path:
                print(f"Batch: {batch_path}")
            for m in err_msgs[:20]:
                print(f"  Error: {m}")
            if len(err_msgs) > 20:
                print(f"  … and {len(err_msgs) - 20} more")
        else:
            uploaded, skipped, err_count, err_msgs = upload_processed_files(
                iter(summaries),
                config=cfg,
                dry_run=args.dry_run,
                include_raw=not args.no_raw,
                force=args.force,
                force_run_ids=force_run_ids,
            )
            if args.dry_run:
                print(f"Processed {uploaded} files (dry-run, not uploaded).")
            else:
                total = uploaded + skipped
                print(f"Found {total} files. Skipped {skipped} (fingerprint match). Uploaded {uploaded}.")
            for m in err_msgs[:20]:
                print(f"  Error: {m}")
    else:
        for summary in summaries:
            print(
                f"{summary.path}: obj={summary.object_id}, "
                f"index_id={summary.index_id}, type={summary.entity_type}, "
                f"period={summary.period_s}s, protocol={summary.protocol.label}"
            )


if __name__ == "__main__":
    main()

