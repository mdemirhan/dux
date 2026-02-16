from __future__ import annotations

import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
import sys
from typing import Annotated

import typer
from rich.console import Console, Group
from rich.markup import escape
from rich.live import Live
from rich.panel import Panel
from rich.spinner import Spinner
from rich.text import Text
from result import Err

from dux.config.defaults import default_config
from dux.config.loader import load_config, sample_config_json
from dux.models.scan import ScanError, ScanErrorCode, ScanOptions, ScanResult
from dux.scan import PythonScanner, Scanner, default_scanner
from dux.services.insights import generate_insights
from dux.services.summary import render_focused_summary, render_summary
from dux.ui.app import DuxApp

console = Console()


@dataclass(slots=True)
class _ScanProgress:
    current_path: str
    files: int
    directories: int
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
        Text.from_markup(f"[#81a2be]Path:[/] {escape(_truncate_path(progress.current_path))}"),
        Text.from_markup(
            f"[#b5bd68]Scanned:[/] {progress.directories:,} dirs, {progress.files:,} files"
            + f"    [#f0c674]Workers:[/] {workers}"
            + f"    [#de935f]Elapsed:[/] {elapsed:.1f}s"
        ),
    )
    return Panel(
        body,
        title="[bold #81a2be]dux - Scanning...[/]",
        border_style="#373b41",
    )


def _scan_with_progress(path: Path, options: ScanOptions, workers: int, scanner: Scanner) -> ScanResult:
    lock = threading.Lock()
    done = threading.Event()
    result: ScanResult | None = None
    progress = _ScanProgress(
        current_path=str(path),
        files=0,
        directories=0,
        start_time=time.perf_counter(),
    )

    def on_progress(current_path: str, files: int, directories: int) -> None:
        with lock:
            progress.current_path = current_path
            progress.files = files
            progress.directories = directories

    def scan_worker() -> None:
        nonlocal result
        try:
            result = scanner.scan(str(path), options, progress_callback=on_progress)
        except Exception as exc:  # noqa: BLE001
            result = Err(
                ScanError(
                    code=ScanErrorCode.INTERNAL,
                    path=str(path),
                    message=f"Unhandled scan failure: {exc}",
                )
            )
        finally:
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
            live.update(_render_scan_panel(snapshot, workers, "Scanning directory tree..."))
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
    top_temp: Annotated[bool, typer.Option("--top-temp", "-t", help="Show largest temp/build artifacts.")] = False,
    top_cache: Annotated[bool, typer.Option("--top-cache", "-c", help="Show largest cache files/directories.")] = False,
    top_dirs: Annotated[bool, typer.Option("--top-dirs", "-d", help="Show largest directories.")] = False,
    top_files: Annotated[bool, typer.Option("--top-files", "-f", help="Show largest files.")] = False,
    interactive: Annotated[bool, typer.Option("--interactive", "-i", help="Launch interactive TUI.")] = False,
    sample_config: Annotated[bool, typer.Option("--sample-config", help="Print sample config JSON.")] = False,
    max_depth: Annotated[int | None, typer.Option("--max-depth", help="Max directory depth to scan.")] = None,
    workers: Annotated[int | None, typer.Option("--workers", "-w", help="Number of scan workers.")] = None,
    top: Annotated[
        int | None,
        typer.Option("--top", help="Number of items in --top-* views."),
    ] = None,
    max_insights: Annotated[int | None, typer.Option("--max-insights", help="Max insights per category.")] = None,
    overview_dirs: Annotated[int | None, typer.Option("--overview-dirs", help="Top directories in overview.")] = None,
    scroll_step: Annotated[int | None, typer.Option("--scroll-step", help="Lines to jump on PgUp/PgDn.")] = None,
    page_size: Annotated[int | None, typer.Option("--page-size", help="Rows per page in TUI.")] = None,
    show_size: Annotated[bool, typer.Option("--show-size", "-s", help="Show logical file size column.")] = False,
    scanner: Annotated[
        str, typer.Option("--scanner", "-S", help="Scanner variant: auto, python, posix, macos.")
    ] = "auto",
) -> None:
    if sys.platform == "win32":
        console.print("[red]Windows support is not implemented yet.[/]")
        raise typer.Exit(1)

    if sample_config:
        console.print(sample_config_json())
        raise typer.Exit(0)

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
        overrides["top_count"] = max(1, top)
    if max_insights is not None:
        overrides["max_insights_per_category"] = max(10, max_insights)
    if overview_dirs is not None:
        overrides["overview_top_dirs"] = max(5, overview_dirs)
    if scroll_step is not None:
        overrides["scroll_step"] = max(1, scroll_step)
    if page_size is not None:
        overrides["page_size"] = max(10, page_size)
    if max_depth is not None:
        overrides["max_depth"] = max(1, max_depth)
    if overrides:
        config = replace(config, **overrides)

    scan_options = ScanOptions(
        max_depth=config.max_depth,
    )

    scanner_impl: Scanner
    if scanner == "auto":
        scanner_impl = default_scanner(workers=config.scan_workers)
    elif scanner == "python":
        scanner_impl = PythonScanner(workers=config.scan_workers)
    elif scanner == "posix":
        from dux.scan.posix_scanner import PosixScanner

        scanner_impl = PosixScanner(workers=config.scan_workers)
    elif scanner == "macos":
        from dux.scan.macos_scanner import MacOSScanner

        scanner_impl = MacOSScanner(workers=config.scan_workers)
    else:
        console.print(f"[red]Unknown scanner: {scanner}. Use: auto, python, posix, macos.[/]")
        raise typer.Exit(1)

    scan_result = _scan_with_progress(Path(path), scan_options, workers=config.scan_workers, scanner=scanner_impl)
    if isinstance(scan_result, Err):
        error = scan_result.unwrap_err()
        console.print(f"[red]Scan failed for {escape(error.path)}: {escape(error.message)}[/]")
        raise typer.Exit(1)
    snapshot = scan_result.unwrap()

    with console.status("[bold #8abeb7]Generating insights...[/]"):
        bundle = generate_insights(snapshot.root, config)

    if interactive:
        DuxApp(
            root=snapshot.root,
            stats=snapshot.stats,
            bundle=bundle,
            config=config,
            show_size=show_size,
        ).run()
        raise typer.Exit(0)

    root_prefix = snapshot.root.path.rstrip("/") + "/"
    if snapshot.stats.access_errors:
        console.print(f"[red]{snapshot.stats.access_errors:,} access errors during scan[/red]")
    render_summary(console, snapshot.root, snapshot.stats, root_prefix, show_size=show_size)
    render_focused_summary(
        console,
        snapshot.root,
        bundle,
        config.top_count,
        root_prefix,
        top_temp=top_temp,
        top_cache=top_cache,
        top_dirs=top_dirs,
        top_files=top_files,
        show_size=show_size,
    )


def cli() -> None:
    typer.run(run)


if __name__ == "__main__":
    cli()
