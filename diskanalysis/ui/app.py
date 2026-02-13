from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, override

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import DataTable, Static

from diskanalysis.config.schema import AppConfig
from diskanalysis.models.enums import InsightCategory, NodeKind
from diskanalysis.models.insight import Insight, InsightBundle
from diskanalysis.models.scan import ScanNode, ScanStats
from diskanalysis.services.formatting import format_bytes, format_ts, relative_bar


TABS = ["overview", "browse", "insights", "temp", "cache"]

_CATEGORY_LABELS: dict[str, str] = {
    "temp": "Temp",
    "cache": "Cache",
    "large_file": "Large File",
    "large_directory": "Large Dir",
    "old_file": "Old File",
    "build_artifact": "Build Artifact",
    "custom": "Custom",
}


@dataclass(slots=True)
class DisplayRow:
    path: str
    name: str
    size_bytes: int
    right: str


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
                "  o / b / i / t / c: Jump to view",
                "",
                "[b #81a2be]Browse[/]",
                "  h / Left: Collapse or parent",
                "  l / Right: Expand or drill in",
                "  Enter: Drill in",
                "  Backspace: Drill out",
                "  Space: Toggle expand/collapse",
                "",
                "[b #81a2be]Search[/]",
                "  /: Start search",
                "  n / N: Next/Prev match",
                "  Enter: Finish search",
                "  Esc: Clear search",
                "",
                "[b #81a2be]Other[/]",
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


class DiskAnalyzerApp(App[None]):
    CSS_PATH = "app.tcss"

    def __init__(
        self,
        root: ScanNode,
        stats: ScanStats,
        bundle: InsightBundle,
        config: AppConfig,
        initial_view: str = "overview",
    ) -> None:
        super().__init__()
        self.root = root
        self.stats = stats
        self.bundle = bundle
        self.config = config
        self.current_view = initial_view if initial_view in TABS else "overview"

        self.node_by_path: dict[str, ScanNode] = {}
        self.parent_by_path: dict[str, str] = {}
        self._index_tree(self.root)

        self.browse_root_path = self.root.path
        self.expanded: set[str] = {self.root.path}

        self.rows: list[DisplayRow] = []
        self.selected_index = 0
        self.search_mode = False
        self.search_query = ""
        self.search_matches: list[int] = []
        self.search_match_cursor = -1
        self.pending_g = False
        self._rows_cache: dict[str, list[DisplayRow]] = {}

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
            Static(id="info-row"),
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

    def _invalidate_browse_rows(self) -> None:
        self._invalidate_rows("browse")

    def _render_header_rows(self) -> None:
        self.query_one("#path-row", Static).update(
            Text.from_markup(f"[#81a2be]Path:[/] {self.root.path}")
        )

        tab_items: list[str] = []
        for tab in TABS:
            label = tab.capitalize()
            if tab == self.current_view:
                tab_items.append(f"[bold #1d1f21 on #b5bd68] {label} [/] ")
            else:
                tab_items.append(f"[#c5c8c6 on #373b41] {label} [/] ")
        self.query_one("#tabs-row", Static).update(
            Text.from_markup(" ".join(tab_items))
        )

    def _render_content_table(self) -> None:
        table = self.query_one("#content-table", DataTable)
        right_header = "MODIFIED" if self.current_view == "browse" else "CATEGORY"
        table.clear(columns=True)

        size_w = 12
        bar_w = 20
        right_w = 16
        name_w = max(20, self.size.width - size_w - bar_w - right_w - 16)
        table.add_column("NAME", width=name_w)
        table.add_column("SIZE", width=size_w)
        table.add_column("BAR", width=bar_w)
        table.add_column(right_header, width=right_w)

        self.rows = self._build_rows_for_current_view()
        if not self.rows:
            self.rows = [
                DisplayRow(path=".", name="(no data)", size_bytes=0, right="-")
            ]

        total = max(
            1,
            self.rows[0].size_bytes
            if self.current_view == "browse"
            else self.root.size_bytes,
        )
        for row in self.rows:
            table.add_row(
                row.name,
                format_bytes(row.size_bytes),
                relative_bar(row.size_bytes, total, 18),
                row.right,
            )

        self.selected_index = max(0, min(self.selected_index, len(self.rows) - 1))
        table.move_cursor(row=self.selected_index, animate=False)

    def _render_footer_rows(self) -> None:
        total_rows = len(self.rows)
        cursor = min(total_rows, self.selected_index + 1)
        info = (
            f"Safe to delete: {format_bytes(self.bundle.safe_reclaimable_bytes)}"
            + f"    Reclaimable: {format_bytes(self.bundle.reclaimable_bytes)}"
            + f"    Row {cursor}/{total_rows}"
        )
        self.query_one("#info-row", Static).update(
            Text.from_markup(f"[#b5bd68]{info}[/]")
        )

        if self.search_mode:
            status = f"SEARCH: /{self.search_query}  (Enter: keep, Esc: clear)"
        else:
            status = "q quit | ? help | Tab views | / search | n/N next/prev | j/k move"
            if self.current_view == "browse":
                status += (
                    " | h/l collapse-expand | Enter drill-in | Backspace drill-out"
                )
        self.query_one("#status-row", Static).update(
            Text.from_markup(f"[#969896]{status}[/]")
        )

    def _build_rows_for_current_view(self) -> list[DisplayRow]:
        cached = self._rows_cache.get(self.current_view)
        if cached is not None:
            return cached

        if self.current_view == "overview":
            rows = self._overview_rows()
        elif self.current_view == "browse":
            rows = self._browse_rows()
        elif self.current_view == "insights":
            rows = self._insight_rows(lambda _: True)
        elif self.current_view == "temp":
            rows = self._insight_rows(
                lambda i: (
                    i.category in {InsightCategory.TEMP, InsightCategory.BUILD_ARTIFACT}
                )
            )
        else:
            rows = self._insight_rows(lambda i: i.category is InsightCategory.CACHE)

        self._rows_cache[self.current_view] = rows
        return rows

    def _overview_rows(self) -> list[DisplayRow]:
        rows: list[DisplayRow] = [
            DisplayRow(
                path="stats.total",
                name=f"Total Size: {format_bytes(self.root.size_bytes)}",
                size_bytes=self.root.size_bytes,
                right="STAT",
            ),
            DisplayRow(
                path="stats.files",
                name=f"Files: {self.stats.files}",
                size_bytes=0,
                right="STAT",
            ),
            DisplayRow(
                path="stats.dirs",
                name=f"Directories: {self.stats.directories}",
                size_bytes=0,
                right="STAT",
            ),
            DisplayRow(
                path="stats.insights",
                name=f"Insights: {len(self.bundle.insights)}",
                size_bytes=0,
                right="STAT",
            ),
            DisplayRow(
                path="stats.safe",
                name=f"Safe to delete: {format_bytes(self.bundle.safe_reclaimable_bytes)}",
                size_bytes=self.bundle.safe_reclaimable_bytes,
                right="STAT",
            ),
            DisplayRow(
                path="stats.reclaim",
                name=f"Reclaimable: {format_bytes(self.bundle.reclaimable_bytes)}",
                size_bytes=self.bundle.reclaimable_bytes,
                right="STAT",
            ),
        ]

        top_items = sorted(
            self.node_by_path.values(), key=lambda x: x.size_bytes, reverse=True
        )
        for node in [item for item in top_items if item.path != self.root.path][
            : self.config.top_n
        ]:
            typ = "DIR" if node.kind is NodeKind.DIRECTORY else "FILE"
            rows.append(
                DisplayRow(
                    path=node.path,
                    name=f"{node.name}",
                    size_bytes=node.size_bytes,
                    right=typ,
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
                    right=format_ts(node.modified_ts),
                )
            )
            if node.kind is NodeKind.DIRECTORY and node.path in self.expanded:
                for child in reversed(node.children):
                    stack.append((child, depth + 1))
        return rows

    def _insight_rows(self, predicate: Callable[[Insight], bool]) -> list[DisplayRow]:
        rows: list[DisplayRow] = []
        root_prefix = self.root.path.rstrip("/") + "/"
        for item in [x for x in self.bundle.insights if predicate(x)]:
            display_path = (
                item.path[len(root_prefix) :]
                if item.path.startswith(root_prefix)
                else item.path
            )
            label = _CATEGORY_LABELS.get(item.category.value, item.category.value)
            rows.append(
                DisplayRow(
                    path=item.path,
                    name=display_path,
                    size_bytes=item.size_bytes,
                    right=label,
                )
            )
        return rows

    def _set_view(self, view: str) -> None:
        if view not in TABS:
            return
        self.current_view = view
        self.selected_index = 0
        self.pending_g = False
        self._refresh_all()

    def _move_selection(self, delta: int) -> None:
        if not self.rows:
            return
        new_index = max(0, min(len(self.rows) - 1, self.selected_index + delta))
        self.selected_index = new_index
        table = self.query_one("#content-table", DataTable)
        table.move_cursor(row=new_index, animate=False)
        self._render_footer_rows()

    def _move_top(self) -> None:
        if not self.rows:
            return
        self.selected_index = 0
        table = self.query_one("#content-table", DataTable)
        table.move_cursor(row=0, animate=False)
        self._render_footer_rows()

    def _move_bottom(self) -> None:
        if not self.rows:
            return
        self.selected_index = max(0, len(self.rows) - 1)
        table = self.query_one("#content-table", DataTable)
        table.move_cursor(row=self.selected_index, animate=False)
        self._render_footer_rows()

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

    def _update_search_matches(self) -> None:
        query = self.search_query.strip().lower()
        if not query:
            self.search_matches = []
            self.search_match_cursor = -1
            return

        self.search_matches = [
            idx
            for idx, row in enumerate(self.rows)
            if query in row.name.lower() or query in row.path.lower()
        ]
        self.search_match_cursor = 0 if self.search_matches else -1
        if self.search_match_cursor >= 0:
            self.selected_index = self.search_matches[self.search_match_cursor]

    def _goto_next_match(self, backward: bool = False) -> None:
        if not self.search_matches:
            return
        if backward:
            self.search_match_cursor = (self.search_match_cursor - 1) % len(
                self.search_matches
            )
        else:
            self.search_match_cursor = (self.search_match_cursor + 1) % len(
                self.search_matches
            )
        self.selected_index = self.search_matches[self.search_match_cursor]
        self._refresh_all()

    @on(DataTable.RowSelected)
    def _on_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.cursor_row != self.selected_index:
            self.selected_index = event.cursor_row
            self._render_footer_rows()

    @on(DataTable.RowHighlighted)
    def _on_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.cursor_row != self.selected_index:
            self.selected_index = event.cursor_row
            self._render_footer_rows()

    def _handle_search_input(self, event) -> None:  # type: ignore[no-untyped-def]
        key = event.key
        if key == "enter":
            self.search_mode = False
            self._refresh_all()
            event.stop()
            return
        if key == "escape":
            self.search_mode = False
            self.search_query = ""
            self.search_matches = []
            self.search_match_cursor = -1
            self._refresh_all()
            event.stop()
            return
        if key == "backspace":
            self.search_query = self.search_query[:-1]
            self._update_search_matches()
            self._refresh_all()
            event.stop()
            return
        if event.character and event.is_printable:
            self.search_query += event.character
            self._update_search_matches()
            self._refresh_all()
            event.stop()

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
        if key in {"o", "b", "i", "t", "c"}:
            mapping = {
                "o": "overview",
                "b": "browse",
                "i": "insights",
                "t": "temp",
                "c": "cache",
            }
            self._set_view(mapping[key])
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
            self._move_selection(10)
            return True
        if key in {"ctrl+u", "pageup"}:
            self._move_selection(-10)
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

    @override
    def on_key(self, event) -> None:  # type: ignore[override]
        key = event.key
        char = event.character or ""

        if self.search_mode:
            self._handle_search_input(event)
            return

        if self._handle_global_key(key):
            return
        if self._handle_navigation_key(key, char):
            return

        if key == "/":
            self.search_mode = True
            self.search_query = ""
            self.search_matches = []
            self.search_match_cursor = -1
            self._refresh_all()
            return

        if (key in {"n", "N", "shift+n"} or char in {"n", "N"}) and self.search_query:
            self._goto_next_match(backward=(key in {"N", "shift+n"} or char == "N"))
            return

        if self.current_view == "browse" and self._handle_browse_key(key):
            return

        if key == "escape" and self.search_query:
            self.search_query = ""
            self.search_matches = []
            self.search_match_cursor = -1
            self._refresh_all()
