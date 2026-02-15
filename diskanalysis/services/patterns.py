from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatch
from functools import lru_cache
from typing import Literal

from diskanalysis.config.schema import PatternRule

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


@lru_cache(maxsize=256)
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


@dataclass(slots=True, frozen=True)
class CompiledRule:
    rule: PatternRule
    matchers: tuple[_Matcher, ...]
    apply_to: Literal["file", "dir", "both"]


def compile_rule(rule: PatternRule) -> CompiledRule:
    expanded = _expand_braces(rule.pattern)
    matchers = tuple(_classify(p) for p in expanded)
    return CompiledRule(rule=rule, matchers=matchers, apply_to=rule.apply_to)


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

# Each entry in the dispatch lists pairs a rule with metadata needed for
# apply_to filtering (pre-split into file-only, dir-only, both).


@dataclass(slots=True, frozen=True)
class _TaggedRule:
    rule: PatternRule
    apply_to: Literal["file", "dir", "both"]


@dataclass(slots=True)
class CompiledRuleSet:
    """All pattern rules from all categories, indexed by matcher kind."""

    # EXACT: basename → list of matching rules (O(1) dict lookup)
    exact_both: dict[str, list[_TaggedRule]] = field(default_factory=dict)
    exact_file: dict[str, list[_TaggedRule]] = field(default_factory=dict)
    exact_dir: dict[str, list[_TaggedRule]] = field(default_factory=dict)

    # CONTAINS: (value, alt, rule) — must iterate but typically short
    contains_both: list[tuple[str, str, _TaggedRule]] = field(default_factory=list)
    contains_file: list[tuple[str, str, _TaggedRule]] = field(default_factory=list)
    contains_dir: list[tuple[str, str, _TaggedRule]] = field(default_factory=list)

    # ENDSWITH: (suffix, rule)
    endswith_both: list[tuple[str, _TaggedRule]] = field(default_factory=list)
    endswith_file: list[tuple[str, _TaggedRule]] = field(default_factory=list)
    endswith_dir: list[tuple[str, _TaggedRule]] = field(default_factory=list)

    # STARTSWITH: (prefix, rule)
    startswith_both: list[tuple[str, _TaggedRule]] = field(default_factory=list)
    startswith_file: list[tuple[str, _TaggedRule]] = field(default_factory=list)
    startswith_dir: list[tuple[str, _TaggedRule]] = field(default_factory=list)

    # GLOB: (pattern, rule) — fallback
    glob_both: list[tuple[str, _TaggedRule]] = field(default_factory=list)
    glob_file: list[tuple[str, _TaggedRule]] = field(default_factory=list)
    glob_dir: list[tuple[str, _TaggedRule]] = field(default_factory=list)

    # Additional path rules (pre-normalized at compile time)
    additional: list[tuple[str, _TaggedRule]] = field(default_factory=list)


def compile_ruleset(
    category_rules: list[list[PatternRule]],
    additional_paths: list[tuple[str, PatternRule]] | None = None,
) -> CompiledRuleSet:
    """Build a single CompiledRuleSet from all categories.

    *category_rules* is a list of rule-lists. Each rule already carries its own
    category.

    *additional_paths* are pre-normalized (base_path, rule) pairs.
    """
    rs = CompiledRuleSet()

    for rules in category_rules:
        for rule in rules:
            cr = compile_rule(rule)
            tagged = _TaggedRule(rule=rule, apply_to=cr.apply_to)

            for m in cr.matchers:
                _add_matcher(rs, m, tagged, cr.apply_to)

    if additional_paths:
        for base, rule in additional_paths:
            tagged = _TaggedRule(rule=rule, apply_to=rule.apply_to)
            rs.additional.append((base, tagged))

    return rs


def _add_matcher(
    rs: CompiledRuleSet,
    m: _Matcher,
    tagged: _TaggedRule,
    apply_to: Literal["file", "dir", "both"],
) -> None:
    if m.kind == _EXACT:
        target = (
            rs.exact_both
            if apply_to == "both"
            else rs.exact_file
            if apply_to == "file"
            else rs.exact_dir
        )
        target.setdefault(m.value, []).append(tagged)
    elif m.kind == _CONTAINS:
        target_list = (
            rs.contains_both
            if apply_to == "both"
            else rs.contains_file
            if apply_to == "file"
            else rs.contains_dir
        )
        target_list.append((m.value, m.alt, tagged))
    elif m.kind == _ENDSWITH:
        target_list = (
            rs.endswith_both
            if apply_to == "both"
            else rs.endswith_file
            if apply_to == "file"
            else rs.endswith_dir
        )
        target_list.append((m.value, tagged))
    elif m.kind == _STARTSWITH:
        target_list = (
            rs.startswith_both
            if apply_to == "both"
            else rs.startswith_file
            if apply_to == "file"
            else rs.startswith_dir
        )
        target_list.append((m.value, tagged))
    else:  # _GLOB
        target_list = (
            rs.glob_both
            if apply_to == "both"
            else rs.glob_file
            if apply_to == "file"
            else rs.glob_dir
        )
        target_list.append((m.value, tagged))


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
    """
    matched: list[PatternRule] = []
    seen: set[str] = set()

    # --- EXACT: O(1) dict lookup ---
    hits = rs.exact_both.get(lbase)
    if hits:
        for tr in hits:
            cat = tr.rule.category.value
            if cat not in seen:
                seen.add(cat)
                matched.append(tr.rule)
    if is_dir:
        hits = rs.exact_dir.get(lbase)
    else:
        hits = rs.exact_file.get(lbase)
    if hits:
        for tr in hits:
            cat = tr.rule.category.value
            if cat not in seen:
                seen.add(cat)
                matched.append(tr.rule)

    # --- CONTAINS: iterate (typically ~15 rules) ---
    for val, alt, tr in rs.contains_both:
        if val in lpath or lpath.endswith(alt):
            cat = tr.rule.category.value
            if cat not in seen:
                seen.add(cat)
                matched.append(tr.rule)
    if is_dir:
        for val, alt, tr in rs.contains_dir:
            if val in lpath or lpath.endswith(alt):
                cat = tr.rule.category.value
                if cat not in seen:
                    seen.add(cat)
                    matched.append(tr.rule)
    else:
        for val, alt, tr in rs.contains_file:
            if val in lpath or lpath.endswith(alt):
                cat = tr.rule.category.value
                if cat not in seen:
                    seen.add(cat)
                    matched.append(tr.rule)

    # --- ENDSWITH ---
    for suffix, tr in rs.endswith_both:
        if lbase.endswith(suffix):
            cat = tr.rule.category.value
            if cat not in seen:
                seen.add(cat)
                matched.append(tr.rule)
    if is_dir:
        for suffix, tr in rs.endswith_dir:
            if lbase.endswith(suffix):
                cat = tr.rule.category.value
                if cat not in seen:
                    seen.add(cat)
                    matched.append(tr.rule)
    else:
        for suffix, tr in rs.endswith_file:
            if lbase.endswith(suffix):
                cat = tr.rule.category.value
                if cat not in seen:
                    seen.add(cat)
                    matched.append(tr.rule)

    # --- STARTSWITH ---
    for prefix, tr in rs.startswith_both:
        if lbase.startswith(prefix):
            cat = tr.rule.category.value
            if cat not in seen:
                seen.add(cat)
                matched.append(tr.rule)
    if is_dir:
        for prefix, tr in rs.startswith_dir:
            if lbase.startswith(prefix):
                cat = tr.rule.category.value
                if cat not in seen:
                    seen.add(cat)
                    matched.append(tr.rule)
    else:
        for prefix, tr in rs.startswith_file:
            if lbase.startswith(prefix):
                cat = tr.rule.category.value
                if cat not in seen:
                    seen.add(cat)
                    matched.append(tr.rule)

    # --- GLOB fallback ---
    for pat, tr in rs.glob_both:
        if _match_pattern_slow(pat, lpath, lbase):
            cat = tr.rule.category.value
            if cat not in seen:
                seen.add(cat)
                matched.append(tr.rule)
    if is_dir:
        for pat, tr in rs.glob_dir:
            if _match_pattern_slow(pat, lpath, lbase):
                cat = tr.rule.category.value
                if cat not in seen:
                    seen.add(cat)
                    matched.append(tr.rule)
    else:
        for pat, tr in rs.glob_file:
            if _match_pattern_slow(pat, lpath, lbase):
                cat = tr.rule.category.value
                if cat not in seen:
                    seen.add(cat)
                    matched.append(tr.rule)

    # --- Additional paths (pre-normalized) ---
    if rs.additional:
        for base, tr in rs.additional:
            if tr.apply_to == "file" and is_dir:
                continue
            if tr.apply_to == "dir" and not is_dir:
                continue
            if raw_path == base or raw_path.startswith(base + "/"):
                cat = tr.rule.category.value
                if cat not in seen:
                    seen.add(cat)
                    matched.append(tr.rule)

    return matched
