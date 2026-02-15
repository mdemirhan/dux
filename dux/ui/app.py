from __future__ import annotations

import shlex
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, override

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.containers import Container, Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Input, Static

from dux.config.schema import AppConfig
from dux.models.enums import InsightCategory, NodeKind
from dux.models.insight import Insight, InsightBundle
from dux.models.scan import ScanNode, ScanStats
from dux.services.formatting import format_bytes, relative_bar
from dux.services.tree import top_nodes


TABS: tuple[str, ...] = ("overview", "browse", "large_dir", "large_file", "temp")

_TAB_LABELS: dict[str, str] = {
    "overview": "Overview",
    "browse": "Browse",
    "temp": "Temporary Files",
    "large_dir": "Directories by Size",
    "large_file": "Files by Size",
}

_CATEGORY_LABELS: dict[str, str] = {
    "temp": "Temp",
    "cache": "Cache",
    "build_artifact": "Build Artifact",
}


@dataclass(slots=True)
class DisplayRow:
    path: str
    name: str
    size_bytes: int
    detail: str
    type_label: str = ""
    disk_usage: int = 0


_PAGED_VIEWS = {"temp", "large_dir", "large_file"}

_TEMP_CATEGORIES = frozenset({InsightCategory.TEMP, InsightCategory.CACHE, InsightCategory.BUILD_ARTIFACT})


class _PagedState:
    __slots__ = ("all_rows", "page_index", "total_items")

    def __init__(self) -> None:
        self.all_rows: list[DisplayRow] | None = None
        self.page_index: int = 0
        self.total_items: int = 0

    @property
    def total_rows(self) -> int:
        return len(self.all_rows) if self.all_rows is not None else 0


class HelpOverlay(ModalScreen[None]):
    CSS = """
    HelpOverlay {
        align: center middle;
        background: rgba(0,0,0,0.45);
    }
    #help-box {
        width: 88%;
        height: 86%;
        background: #282a2e;
        border: solid #81a2be;
        padding: 1 2;
        color: #c5c8c6;
    }
    """

    @override
    def compose(self) -> ComposeResult:
        content = "\n".join(
            [
                "[b #81a2be]Navigation[/]",
                "  j/k or arrows: Move",
                "  gg / G / Home / End: Top/Bottom",
                "  PgUp/PgDn, Ctrl+U/Ctrl+D: Page",
                "",
                "[b #81a2be]Views[/]",
                "  Tab / Shift+Tab: Next/Previous view",
                "  o / b / t / d / f: Jump to view",
                "",
                "[b #81a2be]Browse[/]",
                "  h / Left: Collapse or parent",
                "  l / Right: Expand or drill in",
                "  Enter: Drill in",
                "  Backspace: Drill out",
                "  Space: Toggle expand/collapse",
                "",
                "[b #81a2be]Pagination[/]",
                "  [ / ]: Previous/Next page",
                "",
                "[b #81a2be]Search / Filter[/]",
                "  /: Search / filter rows",
                "  Escape: Clear filter",
                "",
                "[b #81a2be]Other[/]",
                "  y: Yank full path to clipboard",
                "  Y: Yank display text to clipboard",
                "  ?: Toggle help",
                "  q / Ctrl+C: Quit",
            ]
        )
        yield Static(content, id="help-box")

    def key_escape(self) -> None:
        self.dismiss()

    def key_q(self) -> None:
        self.dismiss()

    def key_question_mark(self) -> None:
        self.dismiss()


class SearchOverlay(ModalScreen[str]):
    CSS = """
    SearchOverlay {
        align: center middle;
        background: rgba(0,0,0,0.45);
    }
    #search-box {
        width: 60%;
        height: auto;
        max-height: 9;
        background: #282a2e;
        border: solid #81a2be;
        padding: 1 2;
        color: #c5c8c6;
    }
    #search-label {
        width: 100%;
        color: #81a2be;
        text-style: bold;
        margin-bottom: 1;
    }
    #search-input {
        width: 100%;
    }
    """

    def __init__(self, current_filter: str = "") -> None:
        super().__init__()
        self._current_filter = current_filter

    @override
    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("Filter rows (Enter to apply, Escape to cancel)", id="search-label"),
            Input(
                placeholder="Type to filter…",
                value=self._current_filter,
                id="search-input",
            ),
            id="search-box",
        )

    def on_mount(self) -> None:
        self.query_one("#search-input", Input).focus()

    @on(Input.Submitted)
    def _on_submit(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def key_escape(self) -> None:
        self.dismiss(self._current_filter)


class DuxApp(App[None]):
    CSS_PATH = "app.tcss"

    def __init__(
        self,
        root: ScanNode,
        stats: ScanStats,
        bundle: InsightBundle,
        config: AppConfig,
        initial_view: str = "overview",
        show_size: bool = False,
    ) -> None:
        super().__init__()
        self.root = root
        self.stats = stats
        self.bundle = bundle
        self.config = config
        self._show_size = show_size
        self.current_view = initial_view if initial_view in TABS else "overview"

        self._page_size = config.page_size
        self._scroll_step = config.scroll_step
        self._overview_top = config.overview_top_dirs
        self._top_n_limit = config.max_insights_per_category
        self._root_prefix = root.path.rstrip("/") + "/"

        self.node_by_path: dict[str, ScanNode] = {}
        self.parent_by_path: dict[str, str] = {}
        self._index_tree(self.root)

        self.browse_root_path = self.root.path
        self.expanded: set[str] = {self.root.path}

        self.rows: list[DisplayRow] = []
        self.selected_index = 0
        self.pending_g = False
        self._rows_cache: dict[str, list[DisplayRow]] = {}
        self._paged_states: dict[str, _PagedState] = {v: _PagedState() for v in _PAGED_VIEWS}
        self._view_cursor: dict[str, int] = {}
        self._view_scroll: dict[str, float] = {}
        self._view_filter: dict[str, str] = {}

    def _relative_path(self, absolute_path: str) -> str:
        if absolute_path.startswith(self._root_prefix):
            return absolute_path[len(self._root_prefix) :]
        return absolute_path

    def _index_tree(self, root: ScanNode) -> None:
        stack: list[tuple[ScanNode, str | None]] = [(root, None)]
        while stack:
            node, parent = stack.pop()
            self.node_by_path[node.path] = node
            if parent is not None:
                self.parent_by_path[node.path] = parent
            for child in node.children:
                stack.append((child, node.path))

    @override
    def compose(self) -> ComposeResult:
        yield Container(
            Static(id="path-row"),
            Static(id="tabs-row"),
            Static("─" * 200, id="separator-top"),
            DataTable(id="content-table"),
            Static("─" * 200, id="separator-bottom"),
            Static(id="status-row"),
            id="app-grid",
        )

    def on_mount(self) -> None:
        table = self.query_one("#content-table", DataTable)
        table.cursor_type = "row"
        table.zebra_stripes = True
        table.focus()
        self._refresh_all()

    def on_resize(self) -> None:
        self._refresh_all()

    def _refresh_all(self) -> None:
        self._render_header_rows()
        self._render_content_table()
        self._render_footer_rows()

    def _invalidate_rows(self, view: str) -> None:
        self._rows_cache.pop(view, None)
        if view in _PAGED_VIEWS:
            self._paged_states[view] = _PagedState()

    def _invalidate_browse_rows(self) -> None:
        self._invalidate_rows("browse")

    def _render_header_rows(self) -> None:
        self.query_one("#path-row", Static).update(Text.from_markup(f"[#81a2be]Path:[/] {self.root.path}"))

        tab_items: list[str] = []
        for tab in TABS:
            label = _TAB_LABELS.get(tab, tab)
            if tab == self.current_view:
                tab_items.append(f"[bold #1d1f21 on #b5bd68] {label} [/] ")
            else:
                tab_items.append(f"[#c5c8c6 on #373b41] {label} [/] ")
        self.query_one("#tabs-row", Static).update(Text.from_markup(" ".join(tab_items)))

    def _render_content_table(self) -> None:
        table = self.query_one("#content-table", DataTable)
        table.clear(columns=True)

        col_w = 12
        bar_w = 20
        type_w = 8
        cat_w = 16

        # Each DataTable column adds ~2 chars of cell padding on top of its width.
        extra = (col_w + 2) if self._show_size else 0

        if self.current_view == "temp":
            name_w = max(20, self.size.width - extra - col_w - type_w - cat_w - 16)
            table.add_column("NAME", width=name_w)
            if self._show_size:
                table.add_column("SIZE", width=col_w)
            table.add_column("DISK", width=col_w)
            table.add_column("TYPE", width=type_w)
            table.add_column("CATEGORY", width=cat_w)
        elif self.current_view == "browse":
            name_w = max(20, self.size.width - extra - col_w - bar_w - 12)
            table.add_column("NAME", width=name_w)
            if self._show_size:
                table.add_column("SIZE", width=col_w)
            table.add_column("DISK", width=col_w)
            table.add_column("BAR", width=bar_w)
        else:
            name_w = max(20, self.size.width - extra - col_w - bar_w - 12)
            table.add_column("NAME", width=name_w)
            if self._show_size:
                table.add_column("SIZE", width=col_w)
            table.add_column("DISK", width=col_w)
            table.add_column("BAR", width=bar_w)

        self.rows = self._build_rows_for_current_view()
        if not self.rows:
            self.rows = [DisplayRow(path=".", name="(no data)", size_bytes=0, detail="-")]

        total = max(
            1,
            self.rows[0].disk_usage if self.current_view == "browse" else self.root.disk_usage,
        )
        is_temp = self.current_view == "temp"
        is_browse = self.current_view == "browse"
        for row in self.rows:
            disk_text = format_bytes(row.disk_usage) if row.disk_usage > 0 else ""
            size_text = format_bytes(row.size_bytes) if row.size_bytes > 0 else ""
            bar_text = relative_bar(row.disk_usage, total, 18) if row.disk_usage > 0 else ""
            if is_temp:
                cells: list[str] = [row.name]
                if self._show_size:
                    cells.append(size_text)
                cells.extend([disk_text, row.type_label, row.detail])
                table.add_row(*cells)
            elif is_browse:
                cells = [row.name]
                if self._show_size:
                    cells.append(size_text)
                cells.extend([disk_text, bar_text])
                table.add_row(*cells)
            else:
                cells = [row.name]
                if self._show_size:
                    cells.append(size_text)
                cells.extend([disk_text, bar_text])
                table.add_row(*cells)

        self.selected_index = max(0, min(self.selected_index, len(self.rows) - 1))
        table.move_cursor(row=self.selected_index, animate=False)

    def _render_footer_rows(self) -> None:
        total_rows = len(self.rows)
        cursor = min(total_rows, self.selected_index + 1)

        state = self._paged_states.get(self.current_view)
        if state is not None and state.all_rows is not None:
            paged_total = len(self._filter_rows(state.all_rows))
        else:
            paged_total = state.total_rows if state is not None else 0

        active_filter = self._view_filter.get(self.current_view, "")

        left = f"Row {cursor}/{total_rows}"
        if paged_total > self._page_size:
            total_pages = max(1, (paged_total + self._page_size - 1) // self._page_size)
            assert state is not None
            page_index = state.page_index
            left += f" | Page {page_index + 1}/{total_pages}"
        trimmed_text = self._trimmed_indicator(self.current_view)
        if trimmed_text:
            left += f" | {trimmed_text}"
        if active_filter:
            left += f" | Filter: '{active_filter}'"

        hints = "q quit | ? help | Tab views | / search | y yank path | Y yank name"
        if self.current_view == "browse":
            hints += " | h/l collapse/expand | Enter/Backspace drill-in/out"
        if paged_total > self._page_size:
            hints += " | \\[/] prev/next page"
        if active_filter:
            hints += " | Esc clear filter"

        # Account for horizontal padding: #app-grid (0 1) + #status-row (0 1)
        width = self.size.width - 4
        gap = 4
        max_hints_len = width - len(left) - gap
        if max_hints_len < 10:
            status = left
        else:
            if len(hints) > max_hints_len:
                hints = hints[: max_hints_len - 1] + "…"
            pad = width - len(left) - len(hints)
            status = left + " " * max(gap, pad) + hints

        self.query_one("#status-row", Static).update(Text.from_markup(f"[#969896]{status}[/]"))

    def _build_rows_for_current_view(self) -> list[DisplayRow]:
        if self.current_view in _PAGED_VIEWS:
            return self._paged_view_rows(self.current_view)

        cached = self._rows_cache.get(self.current_view)
        if cached is not None:
            return self._filter_rows(cached)

        if self.current_view == "overview":
            rows = self._overview_rows()
        elif self.current_view == "browse":
            rows = self._browse_rows()
        else:
            rows = []

        self._rows_cache[self.current_view] = rows
        return self._filter_rows(rows)

    def _paged_view_rows(self, view: str) -> list[DisplayRow]:
        state = self._paged_states[view]
        if state.all_rows is None:
            state.all_rows, state.total_items = self._build_all_paged_rows(view)
        filtered = self._filter_rows(state.all_rows)
        filtered_count = len(filtered)
        total_pages = max(1, (filtered_count + self._page_size - 1) // self._page_size)
        state.page_index = max(0, min(state.page_index, total_pages - 1))
        start = state.page_index * self._page_size
        end = start + self._page_size
        return filtered[start:end]

    def _build_all_paged_rows(self, view: str) -> tuple[list[DisplayRow], int]:
        if view == "temp":
            rows = self._insight_rows(lambda i: i.category in _TEMP_CATEGORIES)
            total_items = len(set().union(*(self.bundle.category_paths.get(cat, set()) for cat in _TEMP_CATEGORIES)))
            return rows, total_items
        if view == "large_dir":
            rows = self._top_nodes_rows(NodeKind.DIRECTORY)
            return rows, max(0, self.stats.directories - 1)
        # large_file
        rows = self._top_nodes_rows(NodeKind.FILE)
        return rows, self.stats.files

    def _filtered_page_count(self, state: _PagedState) -> int:
        if state.all_rows is None:
            return 1
        filtered = self._filter_rows(state.all_rows)
        return max(1, (len(filtered) + self._page_size - 1) // self._page_size)

    def _next_page(self) -> None:
        if self.current_view not in _PAGED_VIEWS:
            return
        state = self._paged_states[self.current_view]
        if state.page_index >= self._filtered_page_count(state) - 1:
            return
        state.page_index += 1
        self.selected_index = 0
        self._refresh_all()

    def _prev_page(self) -> None:
        if self.current_view not in _PAGED_VIEWS:
            return
        state = self._paged_states[self.current_view]
        if state.page_index == 0:
            return
        state.page_index -= 1
        self.selected_index = 0
        self._refresh_all()

    def _trimmed_indicator(self, view: str) -> str:
        if view not in _PAGED_VIEWS:
            return ""
        state = self._paged_states[view]
        if state.all_rows is None or state.total_items == 0:
            return ""
        if state.total_rows < state.total_items:
            return f"Showing {state.total_rows:,} of {state.total_items:,} results"
        return f"Showing {state.total_rows:,} results"

    def _filter_rows(self, rows: list[DisplayRow]) -> list[DisplayRow]:
        pattern = self._view_filter.get(self.current_view, "")
        if not pattern:
            return rows
        p = pattern.lower()
        return [r for r in rows if p in r.name.lower() or p in r.path.lower()]

    def _category_size_bytes(self, *categories: InsightCategory) -> int:
        return sum(self.bundle.category_size_bytes.get(cat, 0) for cat in categories)

    def _category_disk_usage(self, *categories: InsightCategory) -> int:
        return sum(self.bundle.category_disk_usage.get(cat, 0) for cat in categories)

    def _overview_rows(self) -> list[DisplayRow]:
        temp_sz = self._category_size_bytes(InsightCategory.TEMP)
        temp_du = self._category_disk_usage(InsightCategory.TEMP)
        cache_sz = self._category_size_bytes(InsightCategory.CACHE)
        cache_du = self._category_disk_usage(InsightCategory.CACHE)
        build_sz = self._category_size_bytes(InsightCategory.BUILD_ARTIFACT)
        build_du = self._category_disk_usage(InsightCategory.BUILD_ARTIFACT)

        rows: list[DisplayRow] = [
            DisplayRow(
                path="",
                name=f"Total Disk: {format_bytes(self.root.disk_usage)}",
                size_bytes=self.root.size_bytes,
                detail="",
                disk_usage=self.root.disk_usage,
            ),
            DisplayRow(
                path="",
                name=f"Files: {self.stats.files:,}",
                size_bytes=0,
                detail="",
            ),
            DisplayRow(
                path="",
                name=f"Directories: {self.stats.directories:,}",
                size_bytes=0,
                detail="",
            ),
            DisplayRow(
                path="",
                name=f"Temp: {format_bytes(temp_du)}",
                size_bytes=temp_sz,
                detail="",
                disk_usage=temp_du,
            ),
            DisplayRow(
                path="",
                name=f"Cache: {format_bytes(cache_du)}",
                size_bytes=cache_sz,
                detail="",
                disk_usage=cache_du,
            ),
            DisplayRow(
                path="",
                name=f"Build Artifacts: {format_bytes(build_du)}",
                size_bytes=build_sz,
                detail="",
                disk_usage=build_du,
            ),
            DisplayRow(
                path="",
                name=f"─────── Largest {self._overview_top} directories ───────",
                size_bytes=0,
                detail="",
            ),
        ]

        top_dirs = top_nodes(self.root, self._overview_top, NodeKind.DIRECTORY)
        for node in top_dirs:
            display_path = self._relative_path(node.path)
            rows.append(
                DisplayRow(
                    path=node.path,
                    name=display_path,
                    size_bytes=node.size_bytes,
                    detail="",
                    disk_usage=node.disk_usage,
                )
            )
        return rows

    def _browse_rows(self) -> list[DisplayRow]:
        browse_root = self.node_by_path.get(self.browse_root_path, self.root)
        rows: list[DisplayRow] = []
        stack: list[tuple[ScanNode, int]] = [(browse_root, 0)]
        while stack:
            node, depth = stack.pop()
            if node.kind is NodeKind.DIRECTORY:
                marker = "▼" if node.path in self.expanded else "▶"
                label = f"{'  ' * depth}{marker} {node.name}"
            else:
                label = f"{'  ' * depth}  {node.name}"
            rows.append(
                DisplayRow(
                    path=node.path,
                    name=label,
                    size_bytes=node.size_bytes,
                    detail="",
                    disk_usage=node.disk_usage,
                )
            )
            if node.kind is NodeKind.DIRECTORY and node.path in self.expanded:
                for child in reversed(node.children):
                    stack.append((child, depth + 1))
        return rows

    def _insight_rows(self, predicate: Callable[[Insight], bool]) -> list[DisplayRow]:
        rows: list[DisplayRow] = []
        for item in self.bundle.insights:
            if not predicate(item):
                continue
            display_path = self._relative_path(item.path)
            label = _CATEGORY_LABELS.get(item.category.value, item.category.value)
            node = self.node_by_path.get(item.path)
            type_label = "Dir" if node is not None and node.is_dir else "File"
            rows.append(
                DisplayRow(
                    path=item.path,
                    name=display_path,
                    size_bytes=item.size_bytes,
                    detail=label,
                    type_label=type_label,
                    disk_usage=item.disk_usage,
                )
            )
        return rows

    def _top_nodes_rows(self, kind: NodeKind) -> list[DisplayRow]:
        rows: list[DisplayRow] = []
        for node in top_nodes(self.root, self._top_n_limit, kind):
            display_path = self._relative_path(node.path)
            rows.append(
                DisplayRow(
                    path=node.path,
                    name=display_path,
                    size_bytes=node.size_bytes,
                    detail="",
                    disk_usage=node.disk_usage,
                )
            )
        return rows

    def _set_view(self, view: str) -> None:
        if view not in TABS:
            return
        # Save current view state before switching.
        self._save_view_state()
        self.current_view = view
        # Restore saved state for the target view (defaults to top).
        self.selected_index = self._view_cursor.get(view, 0)
        self.pending_g = False
        self._refresh_all()
        # Restore scroll offset after table is rebuilt.
        self._restore_scroll(view)

    def _save_view_state(self) -> None:
        """Persist cursor position and scroll offset for the current view."""
        self._view_cursor[self.current_view] = self.selected_index
        table = self.query_one("#content-table", DataTable)
        self._view_scroll[self.current_view] = table.scroll_y

    def _restore_scroll(self, view: str) -> None:
        """Restore saved scroll offset for the given view."""
        saved_y = self._view_scroll.get(view)
        if saved_y is not None and saved_y > 0:
            table = self.query_one("#content-table", DataTable)
            table.scroll_y = saved_y

    def _move_selection(self, delta: int) -> None:
        if not self.rows:
            return
        new_index = max(0, min(len(self.rows) - 1, self.selected_index + delta))
        self.selected_index = new_index
        table = self.query_one("#content-table", DataTable)
        table.move_cursor(row=new_index, animate=False)
        self._render_footer_rows()

    def _move_top(self) -> None:
        self._move_selection(-len(self.rows))

    def _move_bottom(self) -> None:
        self._move_selection(len(self.rows))

    def _sync_selection_from_table(self) -> None:
        if not self.rows:
            self.selected_index = 0
            return
        table = self.query_one("#content-table", DataTable)
        cursor_row = table.cursor_row
        if cursor_row is None:
            return
        self.selected_index = max(0, min(len(self.rows) - 1, cursor_row))

    def _selected_path(self) -> str | None:
        self._sync_selection_from_table()
        if not self.rows:
            return None
        return self.rows[self.selected_index].path

    def _toggle_expand(self) -> None:
        if self.current_view != "browse":
            return
        path = self._selected_path()
        if path is None:
            return
        node = self.node_by_path.get(path)
        if node is None or node.kind is not NodeKind.DIRECTORY:
            return
        if path in self.expanded:
            self.expanded.remove(path)
        else:
            self.expanded.add(path)
        self._invalidate_browse_rows()
        self._refresh_all()

    def _collapse_or_parent(self) -> None:
        if self.current_view != "browse":
            return
        path = self._selected_path()
        if path is None:
            return

        node = self.node_by_path.get(path)
        if (
            node is not None
            and node.kind is NodeKind.DIRECTORY
            and path in self.expanded
            and path != self.browse_root_path
        ):
            self.expanded.remove(path)
            self._invalidate_browse_rows()
            self._refresh_all()
            return

        parent = self.parent_by_path.get(path)
        if parent is None:
            return
        for index, row in enumerate(self.rows):
            if row.path == parent:
                self.selected_index = index
                break
        self._refresh_all()

    def _expand_or_drill(self) -> None:
        if self.current_view != "browse":
            return
        path = self._selected_path()
        if path is None:
            return
        node = self.node_by_path.get(path)
        if node is None or node.kind is not NodeKind.DIRECTORY:
            return

        if path not in self.expanded:
            self.expanded.add(path)
            self._invalidate_browse_rows()
            self._refresh_all()
            return

        self.browse_root_path = path
        self.expanded.add(path)
        self.selected_index = 0
        self._invalidate_browse_rows()
        self._refresh_all()

    def _drill_out(self) -> None:
        if self.current_view != "browse":
            return
        if self.browse_root_path == self.root.path:
            return
        parent = self.parent_by_path.get(self.browse_root_path)
        if parent is None:
            return
        old_root = self.browse_root_path
        self.browse_root_path = parent
        self.selected_index = 0
        self._invalidate_browse_rows()
        for idx, row in enumerate(self._build_rows_for_current_view()):
            if row.path == old_root:
                self.selected_index = idx
                break
        self._refresh_all()

    def _copy_to_clipboard(self, text: str) -> bool:
        if sys.platform == "darwin":
            cmd = ["pbcopy"]
        elif sys.platform == "win32":
            cmd = ["clip"]
        else:
            cmd = ["xclip", "-selection", "clipboard"]
        try:
            subprocess.run(cmd, input=text.encode(), check=True)  # noqa: S603
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    def _yank(self, extract_fn: Callable[[DisplayRow], str]) -> None:
        self._sync_selection_from_table()
        if not self.rows:
            return
        text = extract_fn(self.rows[self.selected_index])
        if self._copy_to_clipboard(text):
            self.notify(f"Copied: {text}", timeout=2)
        else:
            self.notify("Failed to copy to clipboard", severity="error", timeout=2)

    @on(DataTable.RowSelected)
    @on(DataTable.RowHighlighted)
    def _on_row_cursor_changed(self, event: DataTable.RowSelected | DataTable.RowHighlighted) -> None:
        if event.cursor_row != self.selected_index:
            self.selected_index = event.cursor_row
            self._render_footer_rows()

    def _handle_global_key(self, key: str) -> bool:
        if key in {"q", "ctrl+c"}:
            self.exit()
            return True
        if key == "question_mark":
            self.push_screen(HelpOverlay())
            return True
        if key == "tab":
            self._set_view(TABS[(TABS.index(self.current_view) + 1) % len(TABS)])
            return True
        if key in {"shift+tab", "backtab"}:
            self._set_view(TABS[(TABS.index(self.current_view) - 1) % len(TABS)])
            return True
        if key in {"o", "b", "t", "d", "f"}:
            mapping = {
                "o": "overview",
                "b": "browse",
                "t": "temp",
                "d": "large_dir",
                "f": "large_file",
            }
            self._set_view(mapping[key])
            return True
        if key == "slash":
            current = self._view_filter.get(self.current_view, "")
            self.push_screen(SearchOverlay(current), self._on_search_result)
            return True
        return False

    def _handle_navigation_key(self, key: str, char: str) -> bool:
        if key == "j":
            self._move_selection(1)
            return True
        if key == "k":
            self._move_selection(-1)
            return True
        if key in {"ctrl+d", "pagedown"}:
            self._move_selection(self._scroll_step)
            return True
        if key in {"ctrl+u", "pageup"}:
            self._move_selection(-self._scroll_step)
            return True
        if key in {"home", "ctrl+home"}:
            self._move_top()
            return True
        if key in {"end", "ctrl+end"}:
            self._move_bottom()
            return True
        if key == "g" or char == "g":
            if self.pending_g:
                self.pending_g = False
                self._move_top()
            else:
                self.pending_g = True
                self.set_timer(0.5, lambda: setattr(self, "pending_g", False))
            return True
        if key in {"G", "shift+g"} or char == "G":
            self._move_bottom()
            return True
        return False

    def _handle_browse_key(self, key: str) -> bool:
        if key in {"h", "left"}:
            self._collapse_or_parent()
            return True
        if key in {"l", "right"}:
            self._expand_or_drill()
            return True
        if key == "space":
            self._toggle_expand()
            return True
        if key == "enter":
            self._expand_or_drill()
            return True
        if key == "backspace":
            self._drill_out()
            return True
        return False

    def _on_search_result(self, value: str | None) -> None:
        if value:
            self._view_filter[self.current_view] = value
        else:
            self._view_filter.pop(self.current_view, None)
        self.selected_index = 0
        if self.current_view in _PAGED_VIEWS:
            self._paged_states[self.current_view].page_index = 0
        self._refresh_all()

    @override
    def on_key(self, event) -> None:  # type: ignore[override]
        key = event.key
        char = event.character or ""

        if key == "escape":
            if self._view_filter.get(self.current_view):
                self._view_filter.pop(self.current_view, None)
                self.selected_index = 0
                if self.current_view in _PAGED_VIEWS:
                    self._paged_states[self.current_view].page_index = 0
                self._refresh_all()
            return

        if self._handle_global_key(key):
            return
        if key == "y":
            self._yank(lambda row: shlex.quote(row.path) if row.path else shlex.quote(row.name))
            return
        if key in {"Y", "shift+y"} or char == "Y":
            self._yank(lambda row: shlex.quote(row.name))
            return
        if self._handle_navigation_key(key, char):
            return
        state = self._paged_states.get(self.current_view)
        if state is not None and state.total_rows > self._page_size:
            if key == "left_square_bracket" or char == "[":
                self._prev_page()
                return
            if key == "right_square_bracket" or char == "]":
                self._next_page()
                return

        if self.current_view == "browse" and self._handle_browse_key(key):
            return
