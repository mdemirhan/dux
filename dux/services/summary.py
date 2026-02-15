from __future__ import annotations

from rich.console import Console
from rich.table import Table

from dux.models.enums import InsightCategory, NodeKind
from dux.models.insight import Insight, InsightBundle
from dux.models.scan import ScanNode, ScanStats
from dux.services.formatting import format_bytes
from dux.services.insights import filter_insights
from dux.services.tree import top_nodes


def _trim(path: str, root_prefix: str) -> str:
    return path[len(root_prefix) :] if path.startswith(root_prefix) else path


def _insights_table(
    title: str, insights: list[Insight], top_n: int, root_prefix: str, *, show_size: bool = False
) -> Table:
    table = Table(title=title, header_style="bold yellow")
    table.add_column("Path")
    table.add_column("Type", justify="center")
    table.add_column("Category")
    if show_size:
        table.add_column("Size", justify="right")
    table.add_column("Disk", justify="right")
    for item in insights[:top_n]:
        row: list[str] = [
            _trim(item.path, root_prefix),
            "DIR" if item.kind is NodeKind.DIRECTORY else "FILE",
            item.category.value,
        ]
        if show_size:
            row.append(format_bytes(item.size_bytes))
        row.append(format_bytes(item.disk_usage))
        table.add_row(*row)
    return table


def _top_nodes_table(
    title: str, root: ScanNode, top_n: int, kind: NodeKind, root_prefix: str, *, show_size: bool = False
) -> Table:
    table = Table(title=title, header_style="bold yellow")
    table.add_column("Path")
    if show_size:
        table.add_column("Size", justify="right")
    table.add_column("Disk", justify="right")
    for node in top_nodes(root, top_n, kind):
        row: list[str] = [_trim(node.path, root_prefix)]
        if show_size:
            row.append(format_bytes(node.size_bytes))
        row.append(format_bytes(node.disk_usage))
        table.add_row(*row)
    return table


def render_summary(
    console: Console,
    root: ScanNode,
    stats: ScanStats,
    root_prefix: str,
    *,
    show_size: bool = False,
) -> None:
    table = Table(title="Top Level Summary", header_style="bold cyan")
    table.add_column("Path")
    table.add_column("Type", justify="center")
    if show_size:
        table.add_column("Size", justify="right")
    table.add_column("Disk", justify="right")

    for child in sorted(root.children, key=lambda n: n.disk_usage, reverse=True):
        row: list[str] = [
            _trim(child.path, root_prefix),
            "DIR" if child.kind is NodeKind.DIRECTORY else "FILE",
        ]
        if show_size:
            row.append(format_bytes(child.size_bytes))
        row.append(format_bytes(child.disk_usage))
        table.add_row(*row)

    table.add_section()
    total_row: list[str] = ["[bold]Total[/bold]", ""]
    if show_size:
        total_row.append(f"[bold]{format_bytes(root.size_bytes)}[/bold]")
    total_row.append(f"[bold]{format_bytes(root.disk_usage)}[/bold]")
    table.add_row(*total_row)
    table.add_section()
    empty_cols = [""] * (2 if show_size else 1)
    table.add_row(f"[bold]{stats.directories:,}[/bold] dirs", "", *empty_cols)
    table.add_row(f"[bold]{stats.files:,}[/bold] files", "", *empty_cols)

    console.print(table)


def render_focused_summary(
    console: Console,
    root: ScanNode,
    bundle: InsightBundle,
    top_n: int,
    root_prefix: str,
    *,
    top_temp: bool = False,
    top_cache: bool = False,
    top_dirs: bool = False,
    top_files: bool = False,
    show_size: bool = False,
) -> None:
    if top_temp:
        insights = filter_insights(bundle, {InsightCategory.TEMP, InsightCategory.BUILD_ARTIFACT})
        console.print(
            _insights_table("Largest Temporary Files/Directories", insights, top_n, root_prefix, show_size=show_size)
        )
    if top_cache:
        insights = filter_insights(bundle, {InsightCategory.CACHE})
        console.print(
            _insights_table("Largest Cache Files/Directories", insights, top_n, root_prefix, show_size=show_size)
        )

    if top_dirs:
        console.print(
            _top_nodes_table("Largest Directories", root, top_n, NodeKind.DIRECTORY, root_prefix, show_size=show_size)
        )
    if top_files:
        console.print(_top_nodes_table("Largest Files", root, top_n, NodeKind.FILE, root_prefix, show_size=show_size))
