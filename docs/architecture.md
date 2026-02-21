# dux Architecture: End-to-End Scan Pipeline

This document traces the complete lifecycle of a dux scan — from the moment
the user runs `dux /path` to the final display of results.

## Table of Contents

1. [Pipeline Overview](#pipeline-overview)
2. [Phase 1: CLI Entry and Configuration](#phase-1-cli-entry-and-configuration)
3. [Phase 2: Scanner Selection](#phase-2-scanner-selection)
4. [Phase 3: Threaded Scanning](#phase-3-threaded-scanning)
5. [Phase 4: Tree Finalization](#phase-4-tree-finalization)
6. [Phase 5: Pattern Compilation](#phase-5-pattern-compilation)
7. [Phase 6: Insight Generation](#phase-6-insight-generation)
8. [Phase 7: Display](#phase-7-display)
9. [Data Model Reference](#data-model-reference)
10. [Performance Design Decisions](#performance-design-decisions)

---

## Pipeline Overview

```
dux /home/user/projects

  ┌────────────┐   ┌──────────┐   ┌──────────────┐   ┌──────────┐
  │ 1. CLI &   │──▶│ 2. Pick  │──▶│ 3. Threaded  │──▶│ 4. Tree  │
  │ Config     │   │ Scanner  │   │ Scan         │   │ Finalize │
  └────────────┘   └──────────┘   └──────────────┘   └──────────┘
                                                           │
  ┌────────────┐   ┌──────────┐   ┌──────────────┐        │
  │ 7. Display │◀──│ 6. Gen   │◀──│ 5. Compile   │◀───────┘
  │ (CLI/TUI)  │   │ Insights │   │ Patterns     │
  └────────────┘   └──────────┘   └──────────────┘
```

Each phase produces a well-defined output that feeds into the next:

| Phase | Input | Output | Hot path? |
|-------|-------|--------|-----------|
| 1. CLI & Config | argv, config.json | `AppConfig` | No |
| 2. Scanner Selection | platform, GIL state | `ThreadedScannerBase` | No |
| 3. Threaded Scan | root path | `ScanSnapshot` (tree + stats) | Yes |
| 4. Tree Finalize | `ScanNode` tree | sizes aggregated, children sorted | Yes |
| 5. Pattern Compile | `AppConfig.patterns` | `CompiledRuleSet` | No |
| 6. Insight Generation | tree + ruleset | `InsightBundle` | Yes |
| 7. Display | tree + bundle | terminal output or TUI | No |

---

## Phase 1: CLI Entry and Configuration

**Files:** `dux/cli/app.py`, `dux/config/loader.py`, `dux/config/defaults.py`,
`dux/config/schema.py`

### Entry point

```
dux /home/user/projects --interactive --workers 8 --top-temp
```

`dux.cli.app:cli()` is a typer command. It calls `run()` which:

1. **Loads config** from `~/.config/dux/config.json` via `load_config()`.
   If the file doesn't exist, falls back to `default_config()`.
2. **Applies CLI overrides** — command-line flags take precedence over config
   file values. Integer fields are clamped to their minimum (e.g. `workers`
   cannot be less than 1).
3. **Creates `ScanOptions`** with `max_depth` from config.

### Configuration hierarchy

```
Defaults (94 built-in patterns, workers=4, etc.)
    └── config.json overrides
         └── CLI flag overrides
```

### Default patterns

`dux/config/defaults.py` defines 57 pattern rules across three categories:

| Category | Examples | Count |
|----------|----------|-------|
| TEMP | `**/tmp/**`, `**/*.log`, `**/.DS_Store` | 13 |
| CACHE | `**/.npm/**`, `**/.cache/pip/**`, `**/.gradle/**` | 25 |
| BUILD_ARTIFACT | `**/node_modules/**`, `**/.venv/**`, `**/target/**` | 19 |

BUILD_ARTIFACT rules have `stop_recursion=True` — once matched, their
children are not individually scanned for insights (the parent's aggregate
size covers them).

### AppConfig

```python
@dataclass(slots=True)
class AppConfig:
    patterns: list[PatternRule]           # 57 default rules
    additional_paths: dict[...]           # e.g. {CACHE: ["~/.cache"]}
    max_depth: int | None                 # None = unlimited
    scan_workers: int = 4                 # thread count
    top_count: int = 15                   # items in --top-* views
    page_size: int = 100                  # TUI rows per page
    max_insights_per_category: int = 1000 # heap capacity
    overview_top_dirs: int = 100          # dirs in overview tab
    scroll_step: int = 20                 # PgUp/PgDn jump
```

---

## Phase 2: Scanner Selection

**Files:** `dux/scan/__init__.py`, `dux/scan/native_scanner.py`,
`dux/scan/python_scanner.py`

### Decision tree

```
create_scanner(name, workers)
     │
     ├── name == "auto" ──▶ default_scanner(workers)
     │                          │
     │                          ├── macOS?
     │                          │     └── NativeScanner(scan_dir_bulk_nodes)
     │                          │         getattrlistbulk: name+type+size in one syscall
     │                          │
     │                          ├── GIL enabled? (Linux, standard CPython)
     │                          │     └── NativeScanner(scan_dir_nodes)
     │                          │         readdir + lstat, GIL released during I/O
     │                          │
     │                          └── GIL disabled? (free-threaded CPython)
     │                                └── PythonScanner
     │                                    true parallelism makes C overhead negligible
     │
     ├── name == "python" ──▶ PythonScanner(workers)
     ├── name == "posix"  ──▶ NativeScanner(scan_dir_nodes, workers)
     └── name == "macos"  ──▶ NativeScanner(scan_dir_bulk_nodes, workers)
```

### Why three scanners?

The performance characteristics differ by platform and GIL state:

- **macOS `getattrlistbulk`:** Returns name, type, size, and allocated size
  for all entries in a single syscall per 256 KB buffer. Avoids per-entry
  `lstat` calls entirely. Fastest on macOS.

- **POSIX `readdir + lstat`:** Standard two-syscall approach. The C extension
  releases the GIL during I/O, so other Python threads can run. Best when GIL
  is enabled (standard CPython) because the GIL release lets other workers
  make progress.

- **PythonScanner:** Uses `os.scandir()` with cached stat from `DirEntry`.
  Simpler but slower per-directory. Wins on free-threaded Python where true
  parallelism eliminates the need for GIL release tricks.

---

## Phase 3: Threaded Scanning

**Files:** `dux/scan/_base.py`, `csrc/walker.c`, `dux/services/fs.py`,
`dux/models/scan.py`

This is the most performance-critical phase. It reads every file and directory
on disk and builds an in-memory tree.

### Architecture

```
                          ┌──────────────┐
                          │  Main Thread │
                          │              │
                          │  Rich Live   │
                          │  display     │
                          │  (12 Hz)     │
                          └──────┬───────┘
                                 │ progress_callback
                                 │
          ┌──────────────────────┼──────────────────────┐
          │                      │                      │
   ┌──────▼──────┐       ┌──────▼──────┐       ┌──────▼──────┐
   │  Worker 1   │       │  Worker 2   │       │  Worker N   │
   │             │       │             │       │             │
   │  dequeue    │       │  dequeue    │       │  dequeue    │
   │  _scan_dir  │       │  _scan_dir  │       │  _scan_dir  │
   │  enqueue    │       │  enqueue    │       │  enqueue    │
   │  children   │       │  children   │       │  children   │
   └──────┬──────┘       └──────┬──────┘       └──────┬──────┘
          │                      │                      │
          └──────────────────────┼──────────────────────┘
                                 │
                          ┌──────▼──────┐
                          │ _WorkQueue  │
                          │             │
                          │  deque      │
                          │  lock       │
                          │  Condition  │
                          │  _outstanding
                          └─────────────┘
```

### Step-by-step lifecycle

**1. Root validation** (`resolve_root`):
```
/home/user/projects
    ├── expanduser()    → /home/user/projects
    ├── exists()?       → yes
    ├── absolute()      → /home/user/projects
    └── stat()          → is_dir? yes → proceed
```

**2. Create root node and enqueue:**
```python
root_node = ScanNode.directory("/home/user/projects", "projects")
q.put(_Task(root_node, depth=0))
```

**3. Workers loop** — each worker repeats:

```
    dequeue _Task(node, depth)
        │
        ▼
    _scan_dir(node, path)
        │
        │  ┌─────────────────────────────────────────────────┐
        │  │  For NativeScanner (C extension):               │
        │  │                                                  │
        │  │  Phase A: GIL released                          │
        │  │    - readdir() or getattrlistbulk()             │
        │  │    - lstat() per entry (readdir only)           │
        │  │    - Results stored in C EntryBuf               │
        │  │                                                  │
        │  │  Phase B: GIL reacquired                        │
        │  │    - Create ScanNode per entry                  │
        │  │    - Append to parent.children                  │
        │  │    - Collect dir children for enqueueing        │
        │  └─────────────────────────────────────────────────┘
        │
        │  ┌─────────────────────────────────────────────────┐
        │  │  For PythonScanner:                             │
        │  │                                                  │
        │  │    for entry in fs.scandir(path):               │
        │  │        st = entry.stat    ← cached from scandir │
        │  │        if is_dir:                               │
        │  │            ScanNode.directory(...)              │
        │  │        else:                                    │
        │  │            ScanNode.file(...)                   │
        │  │        parent.children.append(node)             │
        │  └─────────────────────────────────────────────────┘
        │
        ▼
    Returns (dir_children, file_count, dir_count, error_count)
        │
        ├── Batch stats locally (avoid lock per file)
        ├── Flush stats under stats_lock (once per directory)
        ├── Depth gate: if depth < max_depth, enqueue children
        └── Emit progress every ~100 items
```

**4. Completion:** When `_outstanding` hits 0, `q.join()` returns. Workers
are shut down and joined.

### Thread safety model

No concurrent mutation of the same data:

- Each directory node is dequeued by exactly **one** worker. That worker has
  exclusive access to `parent.children`.
- `ScanStats` counters are protected by `stats_lock`, but workers batch
  updates locally and flush once per directory to minimize contention.
- `_WorkQueue` uses a single lock with a `Condition` for blocking `get()`.

### The C extension two-phase pattern

```
                    ┌──────────────────────┐
                    │   Python Thread      │
                    │   (holds GIL)        │
                    └─────────┬────────────┘
                              │
                    Py_BEGIN_ALLOW_THREADS
                              │
                    ┌─────────▼────────────┐
                    │   C code (no GIL)    │
                    │                      │
                    │   opendir(path)      │
                    │   while readdir():   │
                    │       lstat()        │
                    │       EntryBuf.push()│
                    │   closedir()         │
                    └─────────┬────────────┘
                              │
                    Py_END_ALLOW_THREADS
                              │
                    ┌─────────▼────────────┐
                    │   C code (has GIL)   │
                    │                      │
                    │   for each entry:    │
                    │     ScanNode(...)     │
                    │     parent.append()   │
                    │                      │
                    │   return results     │
                    └──────────────────────┘
```

The GIL is released during I/O so other Python threads (other scan workers)
can run their Python code. The GIL is reacquired only for creating Python
objects.

### The `_WorkQueue`

A custom lightweight queue that replaces `queue.Queue`:

```
stdlib queue.Queue:
  3 Conditions (not_empty, not_full, all_tasks_done)
  Each Condition wraps its own Lock
  = 6 lock objects total

_WorkQueue:
  1 Lock
  1 Condition (not_empty, shares the Lock)
  1 Event (done)
  = ~2x less contention
```

Key difference: `_WorkQueue` is unbounded (no `not_full` condition) and uses
a simple `_outstanding` counter instead of `all_tasks_done`.

### ScanNode tree structure

```python
@dataclass(slots=True)
class ScanNode:
    path: str               # "/home/user/projects/src/main.py"
    name: str               # "main.py"
    kind: NodeKind           # FILE or DIRECTORY
    size_bytes: int          # logical file size (st_size)
    disk_usage: int          # actual disk blocks (st_blocks * 512)
    children: list[ScanNode] # [] for dirs, LEAF_CHILDREN for files
```

File nodes share an immutable `LEAF_CHILDREN = ()` sentinel instead of each
allocating their own empty list. On a tree with 1 million files, this saves
~56 MB (56 bytes per empty list object).

### Progress display

While scanning runs in background threads, the main thread drives a Rich
Live display at 12 Hz:

```
┌─ dux - Scanning... ──────────────────────────────────────┐
│ ⠋ Scanning directory tree...                             │
│ Path: .../projects/node_modules/react/lib                │
│ Scanned: 4,521 dirs, 89,234 files  Workers: 4  Elapsed: 2.3s │
└──────────────────────────────────────────────────────────┘
```

Progress updates are approximate — workers report every ~100 items to avoid
callback overhead.

---

## Phase 4: Tree Finalization

**File:** `dux/services/tree.py`

After all workers finish, `finalize_sizes(root)` aggregates child sizes
bottom-up.

### Algorithm

A two-pass iterative approach (avoids stack overflow on deep trees):

```
Pass 1: DFS collects directory nodes in pre-order

  projects/            ← stack[0]
  ├── src/             ← stack[1]
  │   ├── lib/         ← stack[2]
  │   └── main.py
  └── docs/            ← stack[3]
      └── readme.md


Pass 2: reversed(stack) gives post-order (leaves before parents)

  docs/    ← aggregate first  (size = readme.md.size)
  lib/     ← aggregate second (size = sum of its files)
  src/     ← aggregate third  (size = lib.size + main.py.size)
  projects/ ← aggregate last  (size = src.size + docs.size)
```

At each directory node:
```python
node.size_bytes = sum(child.size_bytes for child in node.children)
node.disk_usage = sum(child.disk_usage for child in node.children)
node.children.sort(key=lambda x: x.disk_usage, reverse=True)
```

After finalization, children are sorted largest-first for display.

### Result

```python
ScanSnapshot(
    root=root_node,       # fully aggregated tree
    stats=ScanStats(
        files=89234,
        directories=4521,
        access_errors=3,
    ),
)
```

---

## Phase 5: Pattern Compilation

**File:** `dux/services/patterns.py`

This phase converts 57+ glob patterns into optimized data structures. It runs
once at startup, not per file.

### Pipeline

```
PatternRule("npm Cache", "**/.npm/**", CACHE)
    │
    ▼
_expand_braces("**/.npm/**")
    │  → ["**/.npm/**"]  (no braces)
    │
    │  For "**/*.{swp,swo,bak}" this would produce:
    │  → ["**/*.swp", "**/*.swo", "**/*.bak"]
    │
    ▼
_classify("**/.npm/**")
    │  → _Matcher(kind=CONTAINS, value="/.npm/", alt="/.npm")
    │
    ▼
Bucket by apply_to (FILE / DIR / BOTH)
    │  BOTH rules go into both file and dir buckets
    │
    ▼
Build fast-dispatch structures
```

### Classification rules

```
Pattern                     Kind         Fast operation
─────────────────────────   ──────────   ──────────────────────────────
**/name                     EXACT        dict lookup on basename
**/segment/**               CONTAINS     Aho-Corasick on full path
**/*.ext                    ENDSWITH     Aho-Corasick on full path (end-only)
**/prefix*                  STARTSWITH   PrefixTrie on basename
(anything else)             GLOB         fnmatch fallback
```

### CompiledRuleSet structure

```
CompiledRuleSet
  ├── for_file: _ByKind
  │     ├── exact: {"name": [rules...]}          ← O(1) dict
  │     ├── ac: AhoCorasick automaton             ← O(n) single pass
  │     │       keys: "/tmp/", "/tmp", ".log", "/.npm/", ...
  │     ├── prefix_trie: PrefixTrie               ← O(m) single walk
  │     │       keys: "npm-debug.log", ".coverage", ...
  │     ├── glob: [("pattern", rule), ...]        ← O(n*m) fallback
  │     └── additional: [("/home/user/.cache", rule)]
  │
  └── for_dir: _ByKind
        └── (same structure, different rules)
```

CONTAINS and ENDSWITH patterns are merged into a **single** Aho-Corasick
automaton. Each AC value carries an `end_only` flag:

```python
# CONTAINS "**/tmp/**"  →  two keys:
#   "/tmp/"  end_only=False  (match anywhere)
#   "/tmp"   end_only=True   (match only at end of path)

# ENDSWITH "**/*.log"   →  one key:
#   ".log"   end_only=True   (match only at end of path)
```

See [aho-corasick.md](aho-corasick.md) and [prefix-trie.md](prefix-trie.md)
for detailed explanations of these data structures.

---

## Phase 6: Insight Generation

**File:** `dux/services/insights.py`

This phase walks the scan tree, matches every node against the compiled
patterns, and collects the results.

### Pipeline

```
generate_insights(root, config)
    │
    ├── 1. Build additional path rules
    │      ~/.cache → PatternRule("Additional cache path", ...)
    │
    ├── 2. compile_ruleset(patterns, additional_paths)
    │      → CompiledRuleSet (Phase 5)
    │
    ├── 3. DFS traversal with matching
    │      for each node in tree:
    │          match_all(ruleset, lpath, lbase, is_dir)
    │          record insights into heaps + counters
    │
    └── 4. Extract heaps → sorted InsightBundle
```

### The `match_all` hot loop

Called once per node (millions of times on large trees). Five tiers, checked
in order, with first-match-per-category dedup:

```
match_all(ruleset, "/a/tmp/err.log", "err.log", is_dir=False)

  Tier 1: EXACT — dict lookup
    exact["err.log"] → miss

  Tier 2: AC — single automaton pass over full path
    ac.iter("/a/tmp/err.log")
      → hit "/tmp/" at index 5  (CONTAINS, any position → accept)
      → hit "/tmp"  at index 5  (CONTAINS alt, end_only → reject, 5≠13)
      → hit ".log"  at index 13 (ENDSWITH, end_only → accept, 13==13)

  Tier 3: PREFIX TRIE — walk basename
    prefix_trie.iter("err.log")
      → miss (no prefix starts with 'e')

  Tier 4: GLOB — fnmatch fallback
    (no glob rules match)

  Tier 5: ADDITIONAL — path prefix check
    "/a/tmp/err.log" starts with "/home/user/.cache"? → no

  Result: [tmp_rule, log_rule]  (two different categories)
```

### Category dedup

Each tier shares a `seen: set[str]` of category values already matched. Once
a category has a hit, later matches for the same category are skipped:

```python
seen = set()

# Tier 1: EXACT match for category TEMP
seen = {"temp"}

# Tier 2: AC match for category TEMP again → skipped (already in seen)
# Tier 2: AC match for category CACHE → accepted
seen = {"temp", "cache"}
```

This means at most one rule per category is returned.

### Pruning

Two mechanisms prevent wasted work:

**1. Temp/cache pruning:** When a directory matches as TEMP or CACHE, its
children are pushed onto the stack with `in_temp_or_cache=True`. These
children are immediately skipped — the parent's aggregate size already
covers them.

```
/home/user/.cache/          ← matched as CACHE, disk_usage=2.1 GB
  ├── pip/                  ← skipped (parent already covers)
  │   ├── wheels/           ← skipped
  │   └── http/             ← skipped
  └── huggingface/          ← skipped
```

**2. Stop recursion:** BUILD_ARTIFACT rules like `node_modules` have
`stop_recursion=True`. When matched, the directory's children are not
enqueued at all:

```
/home/user/projects/node_modules/   ← matched, stop_recursion=True
  ├── react/                        ← never visited
  ├── lodash/                       ← never visited
  └── (10,000 more packages)        ← never visited
```

### Bounded min-heaps

Insights are stored in per-category min-heaps bounded to
`max_insights_per_category` (default 1000). The heap key is `disk_usage`,
so the smallest item is evicted when the heap is full and a larger item
arrives:

```
Heap (capacity 3, min at top):

  Push 500 MB → [500]
  Push 200 MB → [200, 500]
  Push 800 MB → [200, 500, 800]
  Push 100 MB → rejected (100 < 200, heap[0])
  Push 900 MB → evict 200, push 900 → [500, 800, 900]
```

A `seen` dict per category tracks the highest `disk_usage` per path for
lazy dedup — stale entries may remain in the heap but are filtered during
extraction.

### Result

```python
InsightBundle(
    insights=[                        # sorted by disk_usage descending
        Insight(path="/a/node_modules", disk_usage=1.2GB, category=BUILD_ARTIFACT, ...),
        Insight(path="/a/.cache/pip",   disk_usage=800MB, category=CACHE, ...),
        ...
    ],
    by_category={                     # aggregate totals for overview
        TEMP:           CategoryStats(count=1234, disk_usage=4.5GB, ...),
        CACHE:          CategoryStats(count=567,  disk_usage=2.1GB, ...),
        BUILD_ARTIFACT: CategoryStats(count=89,   disk_usage=8.3GB, ...),
    },
)
```

---

## Phase 7: Display

**Files:** `dux/services/summary.py`, `dux/services/formatting.py`,
`dux/ui/app.py`, `dux/ui/views.py`

### CLI output (default)

```
dux /home/user/projects
```

Renders a Rich table showing the root's immediate children sorted by disk
usage:

```
┌──────────────── Top Level Summary ─────────────────┐
│ Path                    Type    Disk               │
│ node_modules            DIR     1.2 GB             │
│ .venv                   DIR     450.0 MB           │
│ src                     DIR     12.5 MB            │
│ docs                    DIR     1.2 MB             │
│ ─────────────────────────────────────               │
│ Total                           1.7 GB             │
│ 4,521 dirs                                         │
│ 89,234 files                                       │
└────────────────────────────────────────────────────┘
```

Optional `--top-*` flags add focused tables:

| Flag | Shows |
|------|-------|
| `--top-temp` / `-t` | Largest TEMP + BUILD_ARTIFACT insights |
| `--top-cache` / `-c` | Largest CACHE insights |
| `--top-dirs` / `-d` | Largest directories (any category) |
| `--top-files` / `-f` | Largest files (any category) |

### Interactive TUI (`--interactive` / `-i`)

A Textual-based terminal UI with five tabs:

```
┌─ overview ─┬─ browse ─┬─ large dirs ─┬─ large files ─┬─ temp ──┐
│                                                                 │
│  Total Disk: 1.7 GB                                            │
│  Files: 89,234                                                 │
│  Directories: 4,521                                            │
│  Temp: 4.5 GB                                                  │
│  Cache: 2.1 GB                                                 │
│  Build Artifacts: 8.3 GB                                       │
│  ─────── Largest 100 directories ───────                       │
│  node_modules/react          450.0 MB                          │
│  .venv/lib/python3.14        380.0 MB                          │
│  ...                                                            │
└─────────────────────────────────────────────────────────────────┘
```

| Tab | View generator | Data source |
|-----|----------------|-------------|
| overview | `overview_rows()` | tree + `by_category` stats |
| browse | `browse_rows()` | tree (expandable with `▼`/`▶`) |
| large dirs | `top_nodes_rows()` | `top_nodes(root, N, DIRECTORY)` |
| large files | `top_nodes_rows()` | `top_nodes(root, N, FILE)` |
| temp | `insight_rows()` | `InsightBundle.insights` filtered |

Each tab maintains its own `_ViewState` (cursor position, scroll offset,
filter text, cached rows) so switching tabs preserves context.

### DisplayRow

The intermediate representation between data and display:

```python
@dataclass(slots=True)
class DisplayRow:
    path: str           # for clipboard / navigation
    name: str           # display text (may include tree markers)
    size_bytes: int      # apparent size column
    type_label: str      # "Dir" / "File"
    category: str | None # "Temp" / "Cache" / "Build Artifact"
    disk_usage: int      # disk usage column + bar chart
```

### Formatting utilities

```python
format_bytes(1536)       → "1.5 KB"
format_bytes(1073741824) → "1.0 GB"

relative_path("/home/user/projects/src/main.py", "/home/user/projects/")
                         → "src/main.py"

relative_bar(500, 1000, 16)  → "████████░░░░░░░░"
```

---

## Data Model Reference

### Core types

```
ScanNode
  ├── path: str
  ├── name: str
  ├── kind: NodeKind (FILE | DIRECTORY)
  ├── size_bytes: int
  ├── disk_usage: int
  └── children: list[ScanNode]

ScanStats
  ├── files: int
  ├── directories: int
  └── access_errors: int

ScanSnapshot = (root: ScanNode, stats: ScanStats)
```

### Pattern types

```
PatternRule
  ├── name: str                    "npm Cache"
  ├── pattern: str                 "**/.npm/**"
  ├── category: InsightCategory    CACHE
  ├── apply_to: ApplyTo           BOTH (FILE | DIR)
  └── stop_recursion: bool        False

CompiledRuleSet
  ├── for_file: _ByKind
  └── for_dir: _ByKind

_ByKind
  ├── exact: dict[str, list[PatternRule]]
  ├── ac: AhoCorasick | None
  ├── prefix_trie: PrefixTrie | None
  ├── glob: list[tuple[str, PatternRule]]
  └── additional: list[tuple[str, PatternRule]]
```

### Insight types

```
Insight
  ├── path: str
  ├── size_bytes: int
  ├── category: InsightCategory
  ├── summary: str               rule.name ("npm Cache")
  ├── kind: NodeKind
  └── disk_usage: int

CategoryStats
  ├── count: int
  ├── size_bytes: int
  ├── disk_usage: int
  └── paths: set[str]

InsightBundle
  ├── insights: list[Insight]
  └── by_category: dict[InsightCategory, CategoryStats]
```

### Enums

```
NodeKind:        FILE | DIRECTORY
InsightCategory: TEMP | CACHE | BUILD_ARTIFACT
ApplyTo:         FILE=1 | DIR=2 | BOTH=3  (IntFlag for bitwise bucketing)
```

---

## Performance Design Decisions

### Why `disk_usage` and not `size_bytes`?

`size_bytes` is the logical file size (`st_size`). `disk_usage` is the actual
disk blocks allocated (`st_blocks * 512`). A 1-byte file still occupies a 4 KB
block. Sparse files and compression can make them diverge significantly. dux
sorts by `disk_usage` because that's what the user cares about — how much disk
space can be reclaimed.

### Why `LEAF_CHILDREN = ()` instead of `[]`?

File nodes never have children, but each `ScanNode` has a `children` field.
Allocating a new empty `list` per file costs 56 bytes. Sharing an immutable
empty tuple across all file nodes saves ~56 MB on a million-file tree.

### Why batch stat updates?

Workers could update `ScanStats` after every file:
```python
with stats_lock:
    stats.files += 1  # lock acquired 89,234 times
```

Instead, they accumulate locally and flush once per directory:
```python
local_files += 1      # no lock
# ... end of directory ...
with stats_lock:
    stats.files += local_files  # lock acquired 4,521 times
```

This reduces lock contention by ~20x (ratio of files to directories).

### Why `IntFlag` for `ApplyTo`?

`BOTH = FILE | DIR` allows bitwise distribution at compile time:

```python
for flag, builder in builders.items():  # {FILE: ..., DIR: ...}
    if rule.apply_to & flag:             # BOTH & FILE = truthy
        builder.add(matcher, rule)
```

A BOTH rule is added to both builders in a single loop. No `if/elif` on
three enum values. The hot matching loop (`match_all`) never checks
`apply_to` at all — the bucketing was done at compile time.

### Why not recurse for `finalize_sizes`?

Python's default recursion limit is 1000. A filesystem with a 2000-level deep
path (rare but possible with `node_modules` nesting) would crash. The
iterative two-pass approach has no depth limit and uses a flat list instead
of stack frames.

### Why per-category heaps instead of one big list?

The TUI displays insights filtered by category. If we kept one sorted list of
all insights, the `--top-temp` view with `top_count=15` would need to scan
potentially thousands of CACHE entries before finding 15 TEMP entries.
Per-category heaps guarantee O(K log K) extraction where K =
`max_insights_per_category`.
