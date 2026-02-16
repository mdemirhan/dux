from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatch

from dux._matcher import AhoCorasick

from dux.config.schema import PatternRule
from dux.models.enums import ApplyTo

_FILE = ApplyTo.FILE
_DIR = ApplyTo.DIR

# Matcher kinds — integers for fast dispatch in the hot loop.
_CONTAINS = 0  # "/segment/" in path  (for **/segment/**)
_ENDSWITH = 1  # basename.endswith(v) (for **/*.ext)
_STARTSWITH = 2  # basename.startswith(v) (for **/prefix*)
_EXACT = 3  # basename == v         (for **/name)
_GLOB = 4  # fallback to fnmatch


@dataclass(slots=True, frozen=True)
class _Matcher:
    kind: int
    value: str
    alt: str  # _CONTAINS only: endswith variant without trailing /


def _has_glob_chars(s: str) -> bool:
    return "*" in s or "?" in s or "[" in s


def _classify(pattern: str) -> _Matcher:
    """Turn one expanded pattern into a fast string matcher.

    All matcher values are lowercased at compile time so that callers can pass
    pre-lowercased paths for case-insensitive matching with ~4% overhead.
    """
    if not pattern.startswith("**/"):
        return _Matcher(_GLOB, pattern.lower(), "")

    rest = pattern[3:]

    # **/segment/** or **/path/to/thing/**  →  contains check on path
    if rest.endswith("/**"):
        middle = rest[:-3]
        if not _has_glob_chars(middle):
            mid = middle.lower()
            return _Matcher(_CONTAINS, f"/{mid}/", f"/{mid}")
        return _Matcher(_GLOB, pattern.lower(), "")

    # **/*.ext  →  endswith check on basename
    if rest.startswith("*") and not _has_glob_chars(rest[1:]):
        return _Matcher(_ENDSWITH, rest[1:].lower(), "")

    # **/prefix*  →  startswith check on basename
    if rest.endswith("*") and not _has_glob_chars(rest[:-1]):
        return _Matcher(_STARTSWITH, rest[:-1].lower(), "")

    # **/exact  →  exact basename match
    if not _has_glob_chars(rest):
        return _Matcher(_EXACT, rest.lower(), "")

    return _Matcher(_GLOB, pattern.lower(), "")


def _expand_braces(pattern: str) -> tuple[str, ...]:
    start = pattern.find("{")
    end = pattern.find("}", start + 1)
    if start == -1 or end == -1:
        return (pattern,)
    choices = pattern[start + 1 : end].split(",")
    prefix = pattern[:start]
    suffix = pattern[end + 1 :]
    expanded: list[str] = []
    for choice in choices:
        expanded.extend(_expand_braces(f"{prefix}{choice}{suffix}"))
    return tuple(expanded)


def _match_pattern_slow(pattern: str, normalized_path: str, basename: str) -> bool:
    """Fallback for patterns that can't be classified into simple string ops."""
    if pattern.endswith("/**"):
        base_pattern = pattern[: -len("/**")]
        if fnmatch(normalized_path, base_pattern):
            return True
    if fnmatch(normalized_path, pattern):
        return True
    return fnmatch(basename, pattern)


# ---------------------------------------------------------------------------
# CompiledRuleSet — single-pass, hash-based dispatch for all categories
# ---------------------------------------------------------------------------


def _build_ac(
    entries: list[tuple[str, str, PatternRule]],
) -> AhoCorasick | None:
    """Build an Aho-Corasick automaton from CONTAINS matcher entries.

    Each entry is (val, alt, rule).  *val* is an any-position substring;
    *alt* is an end-of-string-only suffix.  The automaton value for each key is
    ``list[tuple[PatternRule, bool]]`` where the bool means *end_only*.
    """
    if not entries:
        return None
    patterns: dict[str, list[tuple[PatternRule, bool]]] = {}
    for val, alt, rule in entries:
        patterns.setdefault(val, []).append((rule, False))
        patterns.setdefault(alt, []).append((rule, True))
    ac = AhoCorasick()
    for key, value in patterns.items():
        ac.add_word(key, value)
    ac.make_automaton()
    return ac


@dataclass(slots=True)
class _ByKind:
    """All pattern rules for one node kind (file or dir), indexed by matcher kind."""

    exact: dict[str, list[PatternRule]] = field(default_factory=dict)
    ac: AhoCorasick | None = None
    endswith: list[tuple[str, PatternRule]] = field(default_factory=list)
    startswith: list[tuple[str, PatternRule]] = field(default_factory=list)
    glob: list[tuple[str, PatternRule]] = field(default_factory=list)
    additional: list[tuple[str, PatternRule]] = field(default_factory=list)


@dataclass(slots=True)
class CompiledRuleSet:
    """All pattern rules from all categories, split by file/dir at compile time."""

    for_file: _ByKind = field(default_factory=_ByKind)
    for_dir: _ByKind = field(default_factory=_ByKind)


def compile_ruleset(
    category_rules: list[list[PatternRule]],
    additional_paths: list[tuple[str, PatternRule]] | None = None,
) -> CompiledRuleSet:
    """Build a single CompiledRuleSet from all categories.

    *category_rules* is a list of rule-lists. Each rule already carries its own
    category.  Rules with ``apply_to=BOTH`` are merged into both file and dir
    collections at compile time so the hot loop never branches on apply_to.

    *additional_paths* are pre-normalized (base_path, rule) pairs.
    """
    # Collect CONTAINS entries locally; they become automata at the end.
    ac_file: list[tuple[str, str, PatternRule]] = []
    ac_dir: list[tuple[str, str, PatternRule]] = []

    exact_file: dict[str, list[PatternRule]] = {}
    exact_dir: dict[str, list[PatternRule]] = {}
    endswith_file: list[tuple[str, PatternRule]] = []
    endswith_dir: list[tuple[str, PatternRule]] = []
    startswith_file: list[tuple[str, PatternRule]] = []
    startswith_dir: list[tuple[str, PatternRule]] = []
    glob_file: list[tuple[str, PatternRule]] = []
    glob_dir: list[tuple[str, PatternRule]] = []

    for rules in category_rules:
        for rule in rules:
            at = rule.apply_to

            for expanded_pat in _expand_braces(rule.pattern):
                m = _classify(expanded_pat)

                if m.kind == _CONTAINS:
                    entry = (m.value, m.alt, rule)
                    if at & _FILE:
                        ac_file.append(entry)
                    if at & _DIR:
                        ac_dir.append(entry)
                elif m.kind == _EXACT:
                    if at & _FILE:
                        exact_file.setdefault(m.value, []).append(rule)
                    if at & _DIR:
                        exact_dir.setdefault(m.value, []).append(rule)
                elif m.kind == _ENDSWITH:
                    pair = (m.value, rule)
                    if at & _FILE:
                        endswith_file.append(pair)
                    if at & _DIR:
                        endswith_dir.append(pair)
                elif m.kind == _STARTSWITH:
                    pair = (m.value, rule)
                    if at & _FILE:
                        startswith_file.append(pair)
                    if at & _DIR:
                        startswith_dir.append(pair)
                else:  # _GLOB
                    pair = (m.value, rule)
                    if at & _FILE:
                        glob_file.append(pair)
                    if at & _DIR:
                        glob_dir.append(pair)

    additional_file: list[tuple[str, PatternRule]] = []
    additional_dir: list[tuple[str, PatternRule]] = []
    if additional_paths:
        for base, rule in additional_paths:
            if rule.apply_to & _FILE:
                additional_file.append((base, rule))
            if rule.apply_to & _DIR:
                additional_dir.append((base, rule))

    return CompiledRuleSet(
        for_file=_ByKind(
            exact=exact_file,
            ac=_build_ac(ac_file),
            endswith=endswith_file,
            startswith=startswith_file,
            glob=glob_file,
            additional=additional_file,
        ),
        for_dir=_ByKind(
            exact=exact_dir,
            ac=_build_ac(ac_dir),
            endswith=endswith_dir,
            startswith=startswith_dir,
            glob=glob_dir,
            additional=additional_dir,
        ),
    )


def match_all(
    rs: CompiledRuleSet,
    lpath: str,
    lbase: str,
    is_dir: bool,
    raw_path: str,
) -> list[PatternRule]:
    """Return all matching rules for a node, one pass across all categories.

    *lpath* and *lbase* must be pre-lowercased.
    *raw_path* is the original-case path for additional path matching.

    Returns at most one rule per category (first match wins).

    Perf: this function is called once per node during the insight traversal
    (millions of times on large trees).  All matching is done via inline
    ``for`` loops instead of list comprehensions to avoid allocating ~10
    temporary lists per call.  Do not refactor back to comprehensions.
    """
    bk = rs.for_dir if is_dir else rs.for_file
    matched: list[PatternRule] = []
    seen: set[str] = set()

    def _try(rule: PatternRule) -> None:
        cat = rule.category.value
        if cat not in seen:
            seen.add(cat)
            matched.append(rule)

    # --- EXACT: O(1) dict lookup ---
    hits = bk.exact.get(lbase)
    if hits:
        for rule in hits:
            _try(rule)

    # --- CONTAINS: Aho-Corasick automaton ---
    if bk.ac is not None:
        _lpath_end = len(lpath) - 1
        for end_idx, entries in bk.ac.iter(lpath):
            for rule, end_only in entries:
                if end_only and end_idx != _lpath_end:
                    continue
                _try(rule)

    # --- ENDSWITH ---
    for suffix, rule in bk.endswith:
        if lbase.endswith(suffix):
            _try(rule)

    # --- STARTSWITH ---
    for prefix, rule in bk.startswith:
        if lbase.startswith(prefix):
            _try(rule)

    # --- GLOB fallback ---
    for pat, rule in bk.glob:
        if _match_pattern_slow(pat, lpath, lbase):
            _try(rule)

    # --- Additional paths (pre-normalized) ---
    if bk.additional:
        for base, rule in bk.additional:
            if raw_path == base or raw_path.startswith(base + "/"):
                _try(rule)

    return matched
