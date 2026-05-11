from __future__ import annotations

from pathlib import Path
from typing import Iterable

import tkinter as tk
from tkinter import filedialog, scrolledtext

from geyser_tool.config import AppConfig
from geyser_tool.pipeline import summarize_clk_files
from geyser_tool.upload import stage_only, upload_batch_to_bigquery, upload_processed_files


def iter_clk_files(paths: Iterable[str | Path]) -> Iterable[Path]:
    for p_str in paths:
        p = Path(p_str)
        if p.is_dir():
            yield from p.rglob("*-CLK.txt")
        elif p.is_file() and p.name.endswith("-CLK.txt"):
            yield p


def run_gui() -> None:
    root = tk.Tk()
    root.title("Geyser Cycler Tool (MVP)")

    frame = tk.Frame(root)
    frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

    path_var = tk.StringVar()

    top_row = tk.Frame(frame)
    top_row.pack(fill=tk.X, pady=(0, 4))

    tk.Label(top_row, text="Folder with CLK logs:").pack(side=tk.LEFT)
    entry = tk.Entry(top_row, textvariable=path_var, width=80)
    entry.pack(side=tk.LEFT, padx=(4, 4), fill=tk.X, expand=True)

    def browse() -> None:
        folder = filedialog.askdirectory(title="Select folder with CLK logs")
        if folder:
            path_var.set(folder)

    tk.Button(top_row, text="Browse…", command=browse).pack(side=tk.LEFT)

    text_area = scrolledtext.ScrolledText(frame, width=100, height=25, state=tk.NORMAL)
    text_area.pack(fill=tk.BOTH, expand=True, pady=(4, 4))

    def log(line: str) -> None:
        text_area.insert(tk.END, line + "\n")
        text_area.see(tk.END)

    summaries_cache: list = []

    def scan() -> None:
        base = Path(path_var.get())
        text_area.delete("1.0", tk.END)
        if not base.exists():
            log(f"Path does not exist: {base}")
            return
        nonlocal summaries_cache
        summaries_cache = list(summarize_clk_files([base]))
        log(f"Found {len(summaries_cache)} CLK files under {base}")

    button_row = tk.Frame(frame)
    button_row.pack(fill=tk.X, pady=(4, 0))

    force_var = tk.BooleanVar(value=False)

    def save_summary() -> None:
        if not summaries_cache:
            log("No summaries to save. Run 'Scan headers' first.")
            return
        save_path = filedialog.asksaveasfilename(
            title="Save summary",
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not save_path:
            return
        lines = []
        for s in summaries_cache:
            lines.append(
                f"{s.path}: obj={s.object_id}, index_id={s.index_id}, "
                f"type={s.entity_type}, period={s.period_s}s, protocol={s.protocol.label}"
            )
        Path(save_path).write_text("\n".join(lines), encoding="utf-8")
        log(f"Saved summary for {len(summaries_cache)} files to {save_path}")

    def update_bigquery() -> None:
        if not summaries_cache:
            log("No summaries to upload. Run 'Scan headers' first.")
            return
        log("Uploading to BigQuery…")
        root.update()
        try:
            uploaded, skipped, err_count, err_msgs = upload_processed_files(
                iter(summaries_cache),
                config=AppConfig.load(),
                dry_run=False,
                force=force_var.get(),
            )
            total = len(summaries_cache)
            log(f"Found {total} files. Skipped {skipped} (fingerprint match). Uploaded {uploaded}.")
            if err_count:
                for m in err_msgs[:10]:
                    log(f"  Error: {m}")
                if len(err_msgs) > 10:
                    log(f"  … and {len(err_msgs) - 10} more")
        except Exception as e:
            log(f"Upload failed: {e}")

    def stage_to_parquet() -> None:
        if not summaries_cache:
            log("No summaries to stage. Run 'Scan headers' first.")
            return
        log("Staging to Parquet…")
        root.update()
        try:
            batch_path, staged_count, skipped_count, err_count, err_msgs = stage_only(
                iter(summaries_cache),
                config=AppConfig.load(),
                include_raw=True,
                force=force_var.get(),
            )
            total = len(summaries_cache)
            log(f"Found {total} files. Staged {staged_count}. Skipped {skipped_count} (fingerprint match). Failed {err_count}.")
            if batch_path:
                log(f"Batch: {batch_path}")
            for m in err_msgs[:10]:
                log(f"  Error: {m}")
            if len(err_msgs) > 10:
                log(f"  … and {len(err_msgs) - 10} more")
        except Exception as e:
            log(f"Stage failed: {e}")

    def upload_batch() -> None:
        cfg = AppConfig.load()
        staging_root = str(cfg.paths.staging_root) if cfg.paths.staging_root else ""
        folder = filedialog.askdirectory(
            title="Select batch folder",
            initialdir=staging_root,
        )
        if not folder:
            return
        log("Uploading batch to BigQuery…")
        root.update()
        try:
            uploaded, err_count, err_msgs = upload_batch_to_bigquery(
                Path(folder),
                config=cfg,
            )
            log(f"Uploaded {uploaded} runs from batch.")
            if err_count:
                for m in err_msgs[:10]:
                    log(f"  Error: {m}")
                if len(err_msgs) > 10:
                    log(f"  … and {len(err_msgs) - 10} more")
        except Exception as e:
            log(f"Upload failed: {e}")

    tk.Button(button_row, text="Scan headers", command=scan).pack(side=tk.LEFT)
    tk.Button(button_row, text="Save summary…", command=save_summary).pack(side=tk.LEFT, padx=(4, 0))
    tk.Button(button_row, text="Stage to Parquet", command=stage_to_parquet).pack(side=tk.LEFT, padx=(4, 0))
    tk.Button(button_row, text="Upload batch…", command=upload_batch).pack(side=tk.LEFT, padx=(4, 0))
    tk.Button(button_row, text="Update BigQuery", command=update_bigquery).pack(side=tk.LEFT, padx=(4, 0))
    tk.Checkbutton(
        button_row,
        text="Force re-upload",
        variable=force_var,
    ).pack(side=tk.LEFT, padx=(8, 0))
    tk.Button(button_row, text="Exit", command=root.destroy).pack(side=tk.RIGHT)

    root.mainloop()


if __name__ == "__main__":
    run_gui()

