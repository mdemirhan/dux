from __future__ import annotations

import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.spinner import Spinner
from rich.text import Text
from result import Err

from diskanalysis.config.defaults import default_config
from diskanalysis.config.loader import load_config, sample_config_json
from diskanalysis.models.enums import InsightCategory, NodeKind
from diskanalysis.models.scan import ScanError, ScanErrorCode, ScanOptions, ScanResult
from diskanalysis.services.insights import filter_insights, generate_insights
from diskanalysis.services.scanner import scan_path
from diskanalysis.services.summary import (
    render_focused_summary,
    render_summary,
    render_top_nodes,
)
from diskanalysis.ui.app import DiskAnalyzerApp

console = Console()


@dataclass(slots=True)
class _ScanProgress:
    current_path: str
    files: int
    directories: int
    updates: int
    start_time: float


def _truncate_path(path: str, max_width: int = 110) -> str:
    if len(path) <= max_width:
        return path
    keep = max_width - 3
    return f"...{path[-keep:]}"


def _render_scan_panel(progress: _ScanProgress, workers: int, phase: str) -> Panel:
    elapsed = time.perf_counter() - progress.start_time
    body = Group(
        Spinner("dots", text=phase, style="bold #8abeb7"),
        Text.from_markup(f"[#81a2be]Path:[/] {_truncate_path(progress.current_path)}"),
        Text.from_markup(
            f"[#b5bd68]Scanned:[/] {progress.directories:,} dirs, {progress.files:,} files"
            + f"    [#f0c674]Workers:[/] {workers}"
            + f"    [#de935f]Elapsed:[/] {elapsed:.1f}s"
        ),
    )
    return Panel(
        body,
        title="[bold #81a2be]Disk Analysis - Scanning...[/]",
        border_style="#373b41",
    )


def _scan_with_progress(path: Path, options: ScanOptions, workers: int) -> ScanResult:
    lock = threading.Lock()
    done = threading.Event()
    result: ScanResult | None = None
    progress = _ScanProgress(
        current_path=str(path),
        files=0,
        directories=0,
        updates=0,
        start_time=time.perf_counter(),
    )

    def on_progress(current_path: str, files: int, directories: int) -> None:
        with lock:
            progress.current_path = current_path
            progress.files = files
            progress.directories = directories
            progress.updates += 1

    def scan_worker() -> None:
        nonlocal result
        result = scan_path(
            path, options, progress_callback=on_progress, workers=workers
        )
        done.set()

    thread = threading.Thread(target=scan_worker, daemon=True)
    thread.start()

    with Live(
        _render_scan_panel(progress, workers, "Scanning directory tree..."),
        console=console,
        refresh_per_second=12,
        transient=True,
    ) as live:
        while not done.is_set():
            with lock:
                snapshot = replace(progress)
            live.update(
                _render_scan_panel(snapshot, workers, "Scanning directory tree...")
            )
            time.sleep(0.08)

        with lock:
            final = replace(progress)
        live.update(_render_scan_panel(final, workers, "Finalizing scan..."))

    thread.join()
    if result is None:
        return Err(
            ScanError(
                code=ScanErrorCode.INTERNAL,
                path=str(path),
                message="Scan did not complete",
            )
        )
    return result


def run(
    path: Annotated[str, typer.Argument(help="Path to analyze.")] = ".",
    temp: Annotated[
        bool, typer.Option("--temp", "-t", help="Focus on temp/build artifacts.")
    ] = False,
    cache: Annotated[
        bool, typer.Option("--cache", "-c", help="Focus on caches.")
    ] = False,
    top_folders: Annotated[
        bool, typer.Option("--top-folders", help="Focus on largest folders.")
    ] = False,
    top_files: Annotated[
        bool, typer.Option("--top-files", help="Focus on largest files.")
    ] = False,
    summary: Annotated[
        bool, typer.Option("--summary", "-s", help="Render non-interactive summary.")
    ] = False,
    sample_config: Annotated[
        bool, typer.Option("--sample-config", help="Print sample config JSON.")
    ] = False,
    max_depth: Annotated[
        int | None, typer.Option("--max-depth", help="Max directory depth to scan.")
    ] = None,
    workers: Annotated[
        int | None, typer.Option("--workers", "-w", help="Number of scan workers.")
    ] = None,
    top: Annotated[
        int | None,
        typer.Option("--top", help="Number of top items to show in summary."),
    ] = None,
    max_insights: Annotated[
        int | None, typer.Option("--max-insights", help="Max insights per category.")
    ] = None,
    overview_folders: Annotated[
        int | None, typer.Option("--overview-folders", help="Top folders in overview.")
    ] = None,
    scroll_step: Annotated[
        int | None, typer.Option("--scroll-step", help="Lines to jump on PgUp/PgDn.")
    ] = None,
    page_size: Annotated[
        int | None, typer.Option("--page-size", help="Rows per page in TUI.")
    ] = None,
    follow_symlinks: Annotated[
        bool, typer.Option("--follow-symlinks", help="Follow symbolic links.")
    ] = False,
) -> None:
    if sample_config:
        console.print(sample_config_json())
        raise typer.Exit(0)

    has_focus = temp or cache or top_folders or top_files

    config_result = load_config()
    if isinstance(config_result, Err):
        console.print(f"[yellow]{config_result.unwrap_err()} Using defaults.[/]")
        config = default_config()
    else:
        config = config_result.unwrap()

    overrides: dict[str, object] = {}
    if workers is not None:
        overrides["scan_workers"] = max(1, workers)
    if top is not None:
        overrides["summary_top_count"] = max(1, top)
    if max_insights is not None:
        overrides["max_insights_per_category"] = max(10, max_insights)
    if overview_folders is not None:
        overrides["overview_top_folders"] = max(5, overview_folders)
    if scroll_step is not None:
        overrides["scroll_step"] = max(1, scroll_step)
    if page_size is not None:
        overrides["page_size"] = max(10, page_size)
    if max_depth is not None:
        overrides["max_depth"] = max(1, max_depth)
    if follow_symlinks:
        overrides["follow_symlinks"] = True
    if overrides:
        config = replace(config, **overrides)

    scan_options = ScanOptions(
        max_depth=config.max_depth,
        follow_symlinks=config.follow_symlinks,
    )

    scan_result = _scan_with_progress(
        Path(path), scan_options, workers=config.scan_workers
    )
    if isinstance(scan_result, Err):
        error = scan_result.unwrap_err()
        console.print(f"[red]Scan failed for {error.path}: {error.message}[/]")
        raise typer.Exit(1)
    snapshot = scan_result.unwrap()

    with console.status("[bold #8abeb7]Generating insights...[/]"):
        bundle = generate_insights(snapshot.root, config)

    if summary:
        if not has_focus:
            render_summary(console, snapshot.root, snapshot.stats, bundle, config)
        else:
            if temp:
                focused = filter_insights(
                    bundle, {InsightCategory.TEMP, InsightCategory.BUILD_ARTIFACT}
                )
                render_focused_summary(
                    console,
                    "Temp / Build Summary",
                    snapshot.root.size_bytes,
                    focused,
                    config.summary_top_count,
                )
            if cache:
                focused = filter_insights(bundle, {InsightCategory.CACHE})
                render_focused_summary(
                    console,
                    "Cache Summary",
                    snapshot.root.size_bytes,
                    focused,
                    config.summary_top_count,
                )
            if top_folders:
                render_top_nodes(
                    console,
                    "Top Folders",
                    snapshot.root,
                    config.summary_top_count,
                    NodeKind.DIRECTORY,
                )
            if top_files:
                render_top_nodes(
                    console,
                    "Top Files",
                    snapshot.root,
                    config.summary_top_count,
                    NodeKind.FILE,
                )
        raise typer.Exit(0)

    initial_view = "overview"
    if temp or cache:
        initial_view = "temp"
    elif top_folders:
        initial_view = "large_dir"
    elif top_files:
        initial_view = "large_file"

    tui = DiskAnalyzerApp(
        root=snapshot.root,
        stats=snapshot.stats,
        bundle=bundle,
        config=config,
        initial_view=initial_view,
    )
    tui.run()


def cli() -> None:
    typer.run(run)


if __name__ == "__main__":
    cli()
