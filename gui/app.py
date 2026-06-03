"""Tkinter GUI shell — two tabs: Fetcher (subprocess) and Analyser (direct calls).

All business logic lives in gui/logic.py (unit-tested). This file is not unit-tested.
Run from project root: python gui/app.py
"""
from __future__ import annotations

import queue
import re
import subprocess
import sys
import threading
from pathlib import Path

import pandas as pd
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox, ttk

import logic

PROJECT_ROOT  = Path(__file__).parent.parent
SPOTIFY_DATA  = PROJECT_ROOT / "spotify_data"
ANALYSED_DATA = PROJECT_ROOT / "analysed_data"

# pytest.ini sets pythonpath for tests; when running app.py directly we must
# add these ourselves so "import analyser" and "import spotify_fetcher" work.
for _d in ("analyser", "fetcher"):
    _p = str(PROJECT_ROOT / _d)
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ---------------------------------------------------------------------------
# Shared widget
# ---------------------------------------------------------------------------

class ScrollableTable(ttk.Frame):
    """Treeview-based table: native frozen header, drag-to-resize columns,
    vertical scroll via mouse wheel, horizontal scroll via Shift+wheel or scrollbar.

    Horizontal scrollbar command uses fractional xview_moveto (2 % steps) so
    the scrollbar thumb moves smoothly instead of jumping one column width at a time.
    stretch=False prevents the Treeview from snapping columns back on resize.
    """

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self.tree = ttk.Treeview(self, show="headings")
        vsb = ttk.Scrollbar(self, orient="vertical",   command=self.tree.yview)
        hsb = ttk.Scrollbar(self, orient="horizontal", command=self._hscroll)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.tree.bind("<Shift-MouseWheel>", self._hwheel)
        # Double-click on a column separator → auto-fit that column
        self.tree.bind("<Double-Button-1>", self._on_header_double_click)

    def _on_header_double_click(self, event: tk.Event) -> None:
        """Auto-fit column width when its right-edge separator is double-clicked."""
        if self.tree.identify_region(event.x, event.y) != "separator":
            return
        col = self.tree.identify_column(event.x)
        if col:
            self._autofit_column(col)

    def _autofit_column(self, col: str) -> None:
        """Resize *col* to the minimum width that displays all content untruncated."""
        font = tkfont.nametofont("TkDefaultFont")
        # Start with the heading text width
        max_w = font.measure(self.tree.heading(col, "text"))
        # Walk every row and measure the cell value
        for iid in self.tree.get_children():
            w = font.measure(str(self.tree.set(iid, col)))
            if w > max_w:
                max_w = w
        # Add cell padding (≈10 px each side; matches default Treeview padding)
        self.tree.column(col, width=max_w + 20)

    def _hscroll(self, *args) -> None:
        lo, hi = self.tree.xview()
        span = hi - lo
        if args[0] == "scroll":
            self.tree.xview_moveto(max(0.0, min(1.0 - span, lo + int(args[1]) * 0.02)))
        else:
            self.tree.xview_moveto(float(args[1]))

    def _hwheel(self, event: tk.Event) -> None:
        lo, hi = self.tree.xview()
        self.tree.xview_moveto(
            max(0.0, min(1.0 - (hi - lo), lo - event.delta / 120 * 0.02))
        )

    def load(self, columns: list[str], rows: list[tuple]) -> None:
        """Full reset: rebuild columns at default widths (120 px) and insert rows."""
        self.tree.delete(*self.tree.get_children())
        self.tree["columns"] = columns
        for col in columns:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=120, minwidth=60, stretch=False)
        for row in rows:
            self.tree.insert("", "end", values=row)

    def reload(self, columns: list[str], rows: list[tuple]) -> None:
        """Reload data preserving manually set column widths.

        Columns that already exist keep their current width; newly added
        columns start at the 120 px default. Use load() to reset all widths.
        """
        saved = {col: self.tree.column(col, "width")
                 for col in self.tree["columns"]}
        self.tree.delete(*self.tree.get_children())
        self.tree["columns"] = columns
        for col in columns:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=saved.get(col, 120), minwidth=60, stretch=False)
        for row in rows:
            self.tree.insert("", "end", values=row)

    def clear(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.tree["columns"] = []


# ---------------------------------------------------------------------------
# Collapsible section widget
# ---------------------------------------------------------------------------

class CollapsibleSection(ttk.Frame):
    """Titled section that collapses/expands when the arrow button is clicked.

    Widget children should be placed in ``self.body`` (supports both pack and grid).
    """

    def __init__(self, parent, text: str, **kwargs):
        super().__init__(parent, **kwargs)
        self._expanded = False

        # Header: ▼/▶ toggle button + title
        hdr = ttk.Frame(self)
        hdr.pack(fill="x")
        self._btn = ttk.Button(hdr, text="▶", width=2, command=self._toggle)
        self._btn.pack(side="left", padx=(0, 4))
        ttk.Label(hdr, text=text).pack(side="left")
        ttk.Separator(self, orient="horizontal").pack(fill="x", pady=(2, 0))

        # Body — place all child widgets here (starts collapsed)
        self.body = ttk.Frame(self)

    def _toggle(self) -> None:
        self._expanded = not self._expanded
        self._btn.configure(text="▼" if self._expanded else "▶")
        if self._expanded:
            self.body.pack(fill="x", padx=4, pady=4)
        else:
            self.body.pack_forget()


# ---------------------------------------------------------------------------
# Fetcher tab
# ---------------------------------------------------------------------------

class FetcherTab(ttk.Frame):
    _PROGRESS_RE = re.compile(r"^\s*\[(\d+)/(\d+)\]")  # matches "[2/6]" lines

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self._queue: queue.Queue = queue.Queue()
        self._proc: subprocess.Popen | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        pad = {"padx": 8, "pady": 4}

        # playlist IDs
        lf_ids = ttk.LabelFrame(self, text="Playlist IDs (one per line)")
        lf_ids.pack(fill="x", **pad)
        self._ids_text = tk.Text(lf_ids, height=4, wrap="none")
        self._ids_text.pack(fill="x", padx=4, pady=4)

        # options
        lf_opts = ttk.LabelFrame(self, text="Options")
        lf_opts.pack(fill="x", **pad)
        ttk.Label(lf_opts, text="Market:").grid(row=0, column=0, sticky="w", padx=4, pady=4)
        self._market_var = tk.StringVar(value="IN")
        ttk.Entry(lf_opts, textvariable=self._market_var, width=6).grid(row=0, column=1, sticky="w", padx=4, pady=4)
        ttk.Label(lf_opts, text="Format:").grid(row=0, column=2, sticky="w", padx=12, pady=4)
        self._fmt_csv  = tk.BooleanVar(value=True)
        self._fmt_json = tk.BooleanVar(value=False)
        self._fmt_pq   = tk.BooleanVar(value=False)
        ttk.Checkbutton(lf_opts, text="csv",     variable=self._fmt_csv).grid(row=0, column=3)
        ttk.Checkbutton(lf_opts, text="json",    variable=self._fmt_json).grid(row=0, column=4)
        ttk.Checkbutton(lf_opts, text="parquet", variable=self._fmt_pq).grid(row=0, column=5)

        # fetch button + progress
        btn_row = ttk.Frame(self)
        btn_row.pack(fill="x", **pad)
        self._fetch_btn = ttk.Button(btn_row, text="Fetch", command=self._on_fetch)
        self._fetch_btn.pack(side="left")
        self._progress = ttk.Progressbar(btn_row, mode="determinate", length=180)
        self._progress.pack(side="left", padx=8)
        self._status_var = tk.StringVar(value="")
        ttk.Label(btn_row, textvariable=self._status_var).pack(side="left", padx=4)

        # output console (no wrap, h+v scroll)
        lf_out = ttk.LabelFrame(self, text="Output")
        lf_out.pack(fill="both", expand=True, **pad)
        out_frame = ttk.Frame(lf_out)
        out_frame.pack(fill="both", expand=True, padx=4, pady=4)
        out_frame.rowconfigure(0, weight=1)
        out_frame.columnconfigure(0, weight=1)
        self._out_text = tk.Text(out_frame, state="disabled", wrap="none", height=18)
        vsb = ttk.Scrollbar(out_frame, orient="vertical",   command=self._out_text.yview)
        hsb = ttk.Scrollbar(out_frame, orient="horizontal", command=self._out_text.xview)
        self._out_text.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self._out_text.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

    def _get_formats(self) -> list[str]:
        fmts = [f for f, v in [("csv", self._fmt_csv), ("json", self._fmt_json), ("parquet", self._fmt_pq)] if v.get()]
        return fmts or ["csv"]

    def _on_fetch(self) -> None:
        cmd = logic.build_fetcher_command(
            self._ids_text.get("1.0", "end").splitlines(),
            self._market_var.get(),
            self._get_formats(),
        )
        if cmd is None:
            messagebox.showerror("No playlists", "Enter at least one playlist ID or URL.")
            return
        self._set_running(True)
        self._clear_output()
        self._append_output(f"Running: {' '.join(cmd)}\n\n")
        threading.Thread(target=self._run_subprocess, args=(cmd,), daemon=True).start()
        self.after(100, self._poll_queue)

    def _run_subprocess(self, cmd: list[str]) -> None:
        try:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, cwd=str(PROJECT_ROOT),
            )
            for line in self._proc.stdout:  # type: ignore[union-attr]
                self._queue.put(("line", line))
            self._proc.wait()
            self._queue.put(("done", self._proc.returncode))
        except Exception as exc:
            self._queue.put(("error", str(exc)))

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "line":
                    self._append_output(payload)
                    m = self._PROGRESS_RE.match(payload)
                    if m:
                        current, total = int(m.group(1)), int(m.group(2))
                        self._progress["maximum"] = total
                        self._progress["value"]   = current
                        self._status_var.set(f"Running — Fetching {current}/{total}")
                elif kind == "done":
                    if payload == 0:
                        self._append_output("\n✓ Fetch complete.")
                        self._status_var.set("Done ✓")
                    else:
                        self._append_output(f"\n✗ Exited with code {payload}.")
                        self._status_var.set(f"Error (rc={payload})")
                    self._set_running(False)
                    return
                elif kind == "error":
                    self._append_output(f"\nError: {payload}")
                    self._set_running(False)
                    return
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _set_running(self, running: bool) -> None:
        self._fetch_btn.configure(state="disabled" if running else "normal")
        if running:
            self._status_var.set("Running…")
            self._progress["value"] = 0
        else:
            self._progress["value"] = 0

    def _append_output(self, text: str) -> None:
        self._out_text.configure(state="normal")
        self._out_text.insert("end", text)
        self._out_text.see("end")
        self._out_text.configure(state="disabled")

    def _clear_output(self) -> None:
        self._out_text.configure(state="normal")
        self._out_text.delete("1.0", "end")
        self._out_text.configure(state="disabled")


# ---------------------------------------------------------------------------
# Analyser tab
# ---------------------------------------------------------------------------

_FILTER_FIELDS: list[tuple[str, str]] = [
    ("track_name",         "Track name"),
    ("artists",            "Artists (comma-sep)"),
    ("artist_ids",         "Artist IDs (comma-sep)"),
    ("album_name",         "Album name"),
    ("album_release_date", "Release date"),
    ("duration",           "Duration (M:SS)"),
    ("track_id",           "Track ID"),
    ("isrc",               "ISRC"),
    ("album_id",           "Album ID"),
    ("added_at",           "Added at"),
    ("added_by",           "Added by"),
]

_SORT_COLS = ["", "track_name", "artists", "album_name", "added_by",
              "duration", "album_release_date", "added_at"]

# GUI filter fields that use substring (contains) matching instead of exact match.
# All other filter fields remain exact / case-sensitive (IDs, ISRC, duration, added_by).
_SUBSTRING_FILTER_FIELDS: frozenset[str] = frozenset({
    "track_name", "artists", "album_name", "album_release_date", "added_at",
})

# Fixed Spotify schema — used to populate the column-selector checkboxes.
_DATA_COLUMNS: list[str] = [
    "track_id", "track_name", "isrc", "artists", "artist_ids",
    "album_name", "album_id", "album_release_date", "added_at", "added_by", "duration",
]


class AnalyserTab(ttk.Frame):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        # Cache hierarchy: load → _loaded_df → filter → _filtered_df → sort/cols → display
        self._loaded_df:       pd.DataFrame | None = None  # raw data after load+dedup
        self._filtered_df:     pd.DataFrame | None = None  # after applying filters
        self._display_result:  pd.DataFrame | None = None  # after sort+col selection
        self._display_columns: list[str] = []
        self._build_ui()
        self._refresh_csv_list()

    def _build_ui(self) -> None:
        pad = {"padx": 8, "pady": 4}

        # data source (multi-select: Ctrl+click or Shift+click)
        lf_file = ttk.LabelFrame(self, text="Data source — Ctrl+click / Shift+click to select multiple")
        lf_file.pack(fill="x", **pad)
        lb_frame = ttk.Frame(lf_file)
        lb_frame.pack(side="left", fill="x", expand=True, padx=4, pady=4)
        self._csv_listbox = tk.Listbox(lb_frame, selectmode="extended", height=4,
                                       exportselection=False, activestyle="none")
        lb_vsb = ttk.Scrollbar(lb_frame, orient="vertical", command=self._csv_listbox.yview)
        self._csv_listbox.configure(yscrollcommand=lb_vsb.set)
        self._csv_listbox.pack(side="left", fill="x", expand=True)
        lb_vsb.pack(side="right", fill="y")
        ttk.Button(lf_file, text="Refresh", command=self._refresh_csv_list).pack(side="left", padx=4, pady=4)

        # filters (collapsible)
        sec_filt = CollapsibleSection(self, text="Filters (leave blank to skip)")
        sec_filt.pack(fill="x", **pad)
        self._filter_vars: dict[str, tk.StringVar] = {}
        for i, (field, label) in enumerate(_FILTER_FIELDS):
            row, col = divmod(i, 2)
            ttk.Label(sec_filt.body, text=label + ":").grid(row=row, column=col * 2, sticky="w", padx=4, pady=2)
            var = tk.StringVar()
            self._filter_vars[field] = var
            var.trace_add("write", lambda *_: self._refresh_filters())
            ttk.Entry(sec_filt.body, textvariable=var, width=24).grid(row=row, column=col * 2 + 1, sticky="ew", padx=4, pady=2)

        # sort
        lf_sort = ttk.LabelFrame(self, text="Sort")
        lf_sort.pack(fill="x", **pad)
        ttk.Label(lf_sort, text="Sort by:").grid(row=0, column=0, sticky="w", padx=4, pady=4)
        self._sort_by_var = tk.StringVar(value="")
        ttk.Combobox(lf_sort, textvariable=self._sort_by_var, values=_SORT_COLS, state="readonly", width=22).grid(row=0, column=1, sticky="w", padx=4)
        ttk.Label(lf_sort, text="Order:").grid(row=0, column=2, sticky="w", padx=12)
        self._sort_order_var = tk.StringVar(value="asc")
        ttk.Combobox(lf_sort, textvariable=self._sort_order_var, values=["asc", "desc"], state="readonly", width=6).grid(row=0, column=3, sticky="w", padx=4)
        self._sort_by_var.trace_add("write",    lambda *_: self._refresh_display())
        self._sort_order_var.trace_add("write",  lambda *_: self._refresh_display())

        # columns — checkbox grid (unchecking all → show all)
        lf_col = ttk.LabelFrame(self, text="Columns to display (uncheck to hide; none checked = show all)")
        lf_col.pack(fill="x", **pad)
        self._col_vars: dict[str, tk.BooleanVar] = {}
        for i, col_name in enumerate(_DATA_COLUMNS):
            r, c = divmod(i, 4)
            var = tk.BooleanVar(value=False)
            self._col_vars[col_name] = var
            var.trace_add("write", lambda *_: self._refresh_display())
            ttk.Checkbutton(lf_col, text=col_name, variable=var).grid(
                row=r, column=c, sticky="w", padx=6, pady=2)

        # save + run
        opt_row = ttk.Frame(self)
        opt_row.pack(fill="x", **pad)
        self._save_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt_row, text="Save results to analysed_data/", variable=self._save_var).pack(side="left")

        btn_row = ttk.Frame(self)
        btn_row.pack(fill="x", **pad)
        ttk.Button(btn_row, text="Analyse", command=self._on_analyse).pack(side="left")
        self._result_label = tk.StringVar(value="")
        ttk.Label(btn_row, textvariable=self._result_label).pack(side="left", padx=8)

        # results table
        self._table = ScrollableTable(self)
        self._table.pack(fill="both", expand=True, **pad)

    def _refresh_csv_list(self) -> None:
        self._csv_listbox.delete(0, "end")
        for p in logic.discover_csvs(SPOTIFY_DATA):
            self._csv_listbox.insert("end", p.name)
        if self._csv_listbox.size():
            self._csv_listbox.selection_set(0)  # pre-select the first file

    def _refresh_filters(self, reset_widths: bool = False) -> None:
        """Re-apply current filter state to _loaded_df, then refresh display.

        Substring fields (track_name, artists, album_name, album_release_date,
        added_at) use case-insensitive contains matching so partial input works.
        All other fields (IDs, ISRC, duration, added_by) remain exact matches
        handled by ana.apply_filters.
        """
        if self._loaded_df is None:
            return
        import analyser as ana  # noqa: PLC0415

        raw_state = {field: var.get() for field, var in self._filter_vars.items()}
        df = self._loaded_df

        # Substring (contains) pass — applied directly with pandas
        for field in _SUBSTRING_FILTER_FIELDS:
            val = raw_state.get(field, "").strip()
            if not val or field not in df.columns:
                continue
            try:
                mask = df[field].astype(str).str.contains(val, case=False, na=False, regex=False)
                df = df[mask]
            except Exception:
                pass  # ignore unexpected column types

        # Exact-match pass — delegated to the analyser for the remaining fields
        exact_raw = {k: v for k, v in raw_state.items() if k not in _SUBSTRING_FILTER_FIELDS}
        exact_active = {k: v for k, v in logic.build_filter_dict(exact_raw).items() if v is not None}
        try:
            df = ana.apply_filters(df, exact_active)
        except Exception:
            return  # silently ignore mid-type validation errors (e.g. partial duration)

        self._filtered_df = df
        self._refresh_display(reset_widths=reset_widths)

    def _refresh_display(self, reset_widths: bool = False) -> None:
        """Re-apply current sort + column selection to the cached filtered DataFrame.

        reset_widths=True  → full column reset (called on Analyse button click).
        reset_widths=False → preserve manually set widths (called on live updates).
        """
        if self._filtered_df is None:
            return
        import analyser as ana  # noqa: PLC0415

        result = self._filtered_df
        sort_by = self._sort_by_var.get().strip()
        if sort_by:
            try:
                result = ana.sort_results(result, sort_by, self._sort_order_var.get() or "asc")
            except Exception:
                pass  # silently ignore mid-change errors during live updates

        checked = [col for col, var in self._col_vars.items() if var.get() and col in result.columns]
        columns = checked if checked else list(result.columns)

        tview_cols, rows = logic.dataframe_to_treeview_rows(result[columns])
        if reset_widths:
            self._table.load(tview_cols, rows)
        else:
            self._table.reload(tview_cols, rows)
        self._result_label.set(f"{len(rows)} track(s)")

        # Cache the processed result so _on_analyse can save it without re-computing
        self._display_result  = result
        self._display_columns = columns

    def _on_analyse(self) -> None:
        import analyser as ana  # noqa: PLC0415

        selected = self._csv_listbox.curselection()
        if not selected:
            messagebox.showerror("No file", "Select at least one data file.")
            return

        all_csvs = list(logic.discover_csvs(SPOTIFY_DATA))
        try:
            dfs = [ana.load_data(str(all_csvs[i])) for i in selected]
        except Exception as exc:
            messagebox.showerror("Load error", str(exc))
            return

        self._loaded_df = dfs[0] if len(dfs) == 1 else (
            pd.concat(dfs, ignore_index=True).drop_duplicates(subset=["track_id"])
        )

        self._refresh_filters(reset_widths=True)

        if self._save_var.get() and self._display_result is not None:
            try:
                ana.save_results(self._display_result, self._display_columns, str(ANALYSED_DATA))
            except Exception as exc:
                messagebox.showerror("Save error", str(exc))


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Spotify App")
        self.geometry("1000x720")
        self.minsize(800, 500)

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=6, pady=6)

        self._fetcher_tab  = FetcherTab(nb)
        self._analyser_tab = AnalyserTab(nb)
        nb.add(self._fetcher_tab,  text="Fetcher")
        nb.add(self._analyser_tab, text="Analyser")

        # refresh CSV list when switching to the Analyser tab
        nb.bind(
            "<<NotebookTabChanged>>",
            lambda _e: self._analyser_tab._refresh_csv_list() if nb.index(nb.select()) == 1 else None,
        )


def main() -> None:
    App().mainloop()


if __name__ == "__main__":
    main()
