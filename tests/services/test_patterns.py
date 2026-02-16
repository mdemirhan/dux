from __future__ import annotations

import pytest

from dux.config.schema import PatternRule
from dux.models.enums import ApplyTo, InsightCategory
from dux.services.patterns import (
    CompiledRuleSet,
    _CONTAINS,
    _ENDSWITH,
    _EXACT,
    _GLOB,
    _STARTSWITH,
    _classify,
    _expand_braces,
    compile_ruleset,
    match_all,
)


# ── _expand_braces ──────────────────────────────────────────────────


def test_expand_braces_no_braces() -> None:
    assert _expand_braces("**/foo/**") == ("**/foo/**",)


def test_expand_braces_simple() -> None:
    assert _expand_braces("**/*.{a,b,c}") == ("**/*.a", "**/*.b", "**/*.c")


def test_expand_braces_finds_first_close_brace() -> None:
    """_expand_braces pairs first '{' with first '}' — not balanced nesting."""
    result = _expand_braces("**/*.{a,{b,c}}")
    # first { at 4, first } at 10 → choices = ["a", "{b", "c"], suffix = "}"
    # → "**/*.a}", "**/*.{b}", "**/*.c}"
    # recursive call on "**/*.{b}" → "**/*.b"
    assert set(result) == {"**/*.a}", "**/*.b", "**/*.c}"}


# ── _classify ───────────────────────────────────────────────────────


def test_classify_contains() -> None:
    m = _classify("**/segment/**")
    assert m.kind == _CONTAINS
    assert m.value == "/segment/"
    assert m.alt == "/segment"


def test_classify_contains_multi_segment() -> None:
    m = _classify("**/path/to/thing/**")
    assert m.kind == _CONTAINS
    assert m.value == "/path/to/thing/"
    assert m.alt == "/path/to/thing"


def test_classify_endswith() -> None:
    m = _classify("**/*.ext")
    assert m.kind == _ENDSWITH
    assert m.value == ".ext"
    assert m.alt == ""


def test_classify_startswith() -> None:
    m = _classify("**/prefix*")
    assert m.kind == _STARTSWITH
    assert m.value == "prefix"
    assert m.alt == ""


def test_classify_exact() -> None:
    m = _classify("**/exactname")
    assert m.kind == _EXACT
    assert m.value == "exactname"
    assert m.alt == ""


def test_classify_no_doublestar_prefix_is_glob() -> None:
    m = _classify("src/*.py")
    assert m.kind == _GLOB
    assert m.value == "src/*.py"


def test_classify_glob_chars_in_contains_fallback() -> None:
    m = _classify("**/foo*bar/**")
    assert m.kind == _GLOB


def test_classify_lowercases_values() -> None:
    m = _classify("**/FooBar/**")
    assert m.value == "/foobar/"
    assert m.alt == "/foobar"

    m2 = _classify("**/*.LOG")
    assert m2.value == ".log"

    m3 = _classify("**/README")
    assert m3.value == "readme"


# ── compile_ruleset / match_all pipeline ────────────────────────────


_APPLY_TO_STR: dict[str, ApplyTo] = {"file": ApplyTo.FILE, "dir": ApplyTo.DIR, "both": ApplyTo.BOTH}


def _rule(
    name: str,
    pattern: str,
    category: InsightCategory = InsightCategory.TEMP,
    apply_to: str = "both",
) -> PatternRule:
    return PatternRule(name=name, pattern=pattern, category=category, apply_to=_APPLY_TO_STR[apply_to])


def test_apply_to_file_does_not_match_dirs() -> None:
    rs = compile_ruleset([[_rule("r", "**/*.log", apply_to="file")]])
    result = match_all(rs, "/a/b/foo.log", "foo.log", is_dir=True, raw_path="/a/b/foo.log")
    assert result == []


def test_apply_to_dir_does_not_match_files() -> None:
    rs = compile_ruleset([[_rule("r", "**/*.egg-info", apply_to="dir")]])
    result = match_all(rs, "/a/foo.egg-info", "foo.egg-info", is_dir=False, raw_path="/a/foo.egg-info")
    assert result == []


def test_apply_to_both_matches_files_and_dirs() -> None:
    rs = compile_ruleset([[_rule("r", "**/node_modules/**")]])
    hit_file = match_all(rs, "/x/node_modules/y", "y", is_dir=False, raw_path="/x/node_modules/y")
    hit_dir = match_all(rs, "/x/node_modules/y", "y", is_dir=True, raw_path="/x/node_modules/y")
    assert len(hit_file) == 1
    assert len(hit_dir) == 1


def test_first_match_wins_dedup_by_category() -> None:
    """Only one rule per category is returned."""
    rs = compile_ruleset(
        [
            [
                _rule("r1", "**/foo", InsightCategory.TEMP),
                _rule("r2", "**/foo", InsightCategory.TEMP),
            ]
        ]
    )
    result = match_all(rs, "/x/foo", "foo", is_dir=False, raw_path="/x/foo")
    assert len(result) == 1
    assert result[0].name == "r1"


def test_multiple_categories_all_returned() -> None:
    rs = compile_ruleset(
        [
            [_rule("t", "**/foo", InsightCategory.TEMP)],
            [_rule("c", "**/foo", InsightCategory.CACHE)],
        ]
    )
    result = match_all(rs, "/x/foo", "foo", is_dir=False, raw_path="/x/foo")
    cats = {r.category for r in result}
    assert cats == {InsightCategory.TEMP, InsightCategory.CACHE}


def test_contains_mid_path() -> None:
    """CONTAINS val (with slashes) fires anywhere in path."""
    rs = compile_ruleset([[_rule("r", "**/tmp/**")]])
    result = match_all(rs, "/a/tmp/b/c", "c", is_dir=False, raw_path="/a/tmp/b/c")
    assert len(result) == 1


def test_contains_end_only_alt() -> None:
    """CONTAINS alt (without trailing /) only fires at end of path."""
    rs = compile_ruleset([[_rule("r", "**/tmp/**")]])
    # Path ending with /tmp — alt "/tmp" matches at end
    result = match_all(rs, "/a/tmp", "tmp", is_dir=True, raw_path="/a/tmp")
    assert len(result) == 1


def test_contains_alt_does_not_fire_mid_path() -> None:
    """Alt suffix without trailing / must be at end of path to match."""
    # The alt "/tmp" pattern is end_only=True. In a path like "/a/tmp/b",
    # the alt would match at position 5 but that's not the end, so no match.
    # However, the val "/tmp/" pattern matches mid-path.
    rs = compile_ruleset([[_rule("r", "**/tmp/**")]])
    result = match_all(rs, "/a/tmp/b", "b", is_dir=False, raw_path="/a/tmp/b")
    assert len(result) == 1  # matched via val "/tmp/", not alt


def test_exact_match_on_basename() -> None:
    rs = compile_ruleset([[_rule("r", "**/.DS_Store", apply_to="file")]])
    result = match_all(rs, "/a/b/.ds_store", ".ds_store", is_dir=False, raw_path="/a/b/.DS_Store")
    assert len(result) == 1


def test_endswith_match() -> None:
    rs = compile_ruleset([[_rule("r", "**/*.log", apply_to="file")]])
    result = match_all(rs, "/a/b/foo.log", "foo.log", is_dir=False, raw_path="/a/b/foo.log")
    assert len(result) == 1


# ── ENDSWITH via Aho-Corasick ────────────────────────────────────────
#
# ENDSWITH patterns (e.g. **/*.log) are compiled into the AC automaton
# with end_only=True.  The suffix is matched against lpath; since lpath
# always ends with the basename, end_idx == len(lpath) - 1 is equivalent
# to basename.endswith(suffix).


class TestEndswithAC:
    """ENDSWITH patterns go through the Aho-Corasick automaton as end-only keys."""

    def test_suffix_does_not_match_mid_path(self) -> None:
        """A mid-path occurrence of the suffix must not trigger a match.

        Path: /a/foo.log/bar  — .log appears mid-path but bar is the basename.
        """
        rs = compile_ruleset([[_rule("r", "**/*.log", apply_to="file")]])
        result = match_all(rs, "/a/foo.log/bar", "bar", is_dir=False, raw_path="/a/foo.log/bar")
        assert result == []

    def test_suffix_matches_at_end_of_path(self) -> None:
        rs = compile_ruleset([[_rule("r", "**/*.log")]])
        result = match_all(rs, "/a/b/error.log", "error.log", is_dir=False, raw_path="/a/b/error.log")
        assert len(result) == 1
        assert result[0].name == "r"

    def test_multiple_endswith_suffixes(self) -> None:
        """Two different ENDSWITH patterns coexist in the same AC automaton."""
        rs = compile_ruleset(
            [
                [
                    _rule("log", "**/*.log", apply_to="file"),
                    _rule("bak", "**/*.bak", apply_to="file"),
                ]
            ]
        )
        log_hit = match_all(rs, "/a/x.log", "x.log", is_dir=False, raw_path="/a/x.log")
        bak_hit = match_all(rs, "/a/x.bak", "x.bak", is_dir=False, raw_path="/a/x.bak")
        txt_miss = match_all(rs, "/a/x.txt", "x.txt", is_dir=False, raw_path="/a/x.txt")
        assert len(log_hit) == 1 and log_hit[0].name == "log"
        assert len(bak_hit) == 1 and bak_hit[0].name == "bak"
        assert txt_miss == []

    def test_endswith_apply_to_dir(self) -> None:
        """ENDSWITH with apply_to=dir matches directories but not files."""
        rs = compile_ruleset([[_rule("r", "**/*.egg-info", apply_to="dir")]])
        dir_hit = match_all(rs, "/a/foo.egg-info", "foo.egg-info", is_dir=True, raw_path="/a/foo.egg-info")
        file_miss = match_all(rs, "/a/foo.egg-info", "foo.egg-info", is_dir=False, raw_path="/a/foo.egg-info")
        assert len(dir_hit) == 1
        assert file_miss == []

    def test_endswith_case_insensitive(self) -> None:
        """Suffix is lowercased at compile time; lpath must be pre-lowercased."""
        rs = compile_ruleset([[_rule("r", "**/*.LOG")]])
        result = match_all(rs, "/a/b/error.log", "error.log", is_dir=False, raw_path="/a/b/error.LOG")
        assert len(result) == 1

    def test_endswith_brace_expansion(self) -> None:
        """Brace-expanded ENDSWITH patterns each get their own AC key."""
        rs = compile_ruleset([[_rule("r", "**/*.{swp,swo,bak}")]])
        swp = match_all(rs, "/a/f.swp", "f.swp", is_dir=False, raw_path="/a/f.swp")
        swo = match_all(rs, "/a/f.swo", "f.swo", is_dir=False, raw_path="/a/f.swo")
        bak = match_all(rs, "/a/f.bak", "f.bak", is_dir=False, raw_path="/a/f.bak")
        py = match_all(rs, "/a/f.py", "f.py", is_dir=False, raw_path="/a/f.py")
        assert len(swp) == 1
        assert len(swo) == 1
        assert len(bak) == 1
        assert py == []

    def test_endswith_dedup_by_category(self) -> None:
        """Two ENDSWITH rules in the same category — first match wins."""
        rs = compile_ruleset(
            [
                [
                    _rule("first", "**/*.log", InsightCategory.TEMP),
                    _rule("second", "**/*.log", InsightCategory.TEMP),
                ]
            ]
        )
        result = match_all(rs, "/a/x.log", "x.log", is_dir=False, raw_path="/a/x.log")
        assert len(result) == 1
        assert result[0].name == "first"

    def test_endswith_different_categories(self) -> None:
        """ENDSWITH rules in different categories both match."""
        rs = compile_ruleset(
            [
                [_rule("t", "**/*.log", InsightCategory.TEMP)],
                [_rule("c", "**/*.log", InsightCategory.CACHE)],
            ]
        )
        result = match_all(rs, "/a/x.log", "x.log", is_dir=False, raw_path="/a/x.log")
        cats = {r.category for r in result}
        assert cats == {InsightCategory.TEMP, InsightCategory.CACHE}

    def test_endswith_coexists_with_contains(self) -> None:
        """ENDSWITH and CONTAINS patterns share one AC automaton."""
        rs = compile_ruleset(
            [
                [_rule("tmp", "**/tmp/**", InsightCategory.TEMP)],
                [_rule("log", "**/*.log", InsightCategory.CACHE, apply_to="file")],
            ]
        )
        # File inside tmp with .log extension — both should match (different categories)
        result = match_all(rs, "/a/tmp/err.log", "err.log", is_dir=False, raw_path="/a/tmp/err.log")
        names = {r.name for r in result}
        assert "tmp" in names
        assert "log" in names

    def test_endswith_partial_suffix_no_match(self) -> None:
        """Suffix .log should not match a file ending with .logx."""
        rs = compile_ruleset([[_rule("r", "**/*.log")]])
        result = match_all(rs, "/a/x.logx", "x.logx", is_dir=False, raw_path="/a/x.logx")
        assert result == []

    def test_endswith_populates_ac(self) -> None:
        """An ENDSWITH-only ruleset still builds an AC automaton."""
        rs = compile_ruleset([[_rule("r", "**/*.log", apply_to="file")]])
        assert rs.for_file.ac is not None


# ── CONTAINS via Aho-Corasick ────────────────────────────────────────
#
# CONTAINS patterns (e.g. **/node_modules/**) produce two AC keys:
#   val = "/node_modules/"  (end_only=False) — matches anywhere in the path
#   alt = "/node_modules"   (end_only=True)  — matches only at end of path
#
# The val key handles children inside the directory.  The alt key handles
# the directory entry itself (path ends with the segment).


class TestContainsAC:
    """CONTAINS patterns go through the Aho-Corasick automaton."""

    def test_no_partial_segment_match(self) -> None:
        """Segment boundaries matter: /tmpdir/ must not match /tmp/ pattern."""
        rs = compile_ruleset([[_rule("r", "**/tmp/**")]])
        result = match_all(rs, "/a/tmpdir/b", "b", is_dir=False, raw_path="/a/tmpdir/b")
        assert result == []

    def test_segment_at_start_of_path(self) -> None:
        """Contains segment right after root still matches."""
        rs = compile_ruleset([[_rule("r", "**/tmp/**")]])
        result = match_all(rs, "/tmp/foo", "foo", is_dir=False, raw_path="/tmp/foo")
        assert len(result) == 1

    def test_deeply_nested_match(self) -> None:
        rs = compile_ruleset([[_rule("r", "**/node_modules/**")]])
        result = match_all(
            rs,
            "/a/b/c/node_modules/d/e/f",
            "f",
            is_dir=False,
            raw_path="/a/b/c/node_modules/d/e/f",
        )
        assert len(result) == 1

    def test_multiple_contains_patterns(self) -> None:
        """Two CONTAINS patterns in different categories both fire."""
        rs = compile_ruleset(
            [
                [_rule("tmp", "**/tmp/**", InsightCategory.TEMP)],
                [_rule("cache", "**/.cache/**", InsightCategory.CACHE)],
            ]
        )
        # A path that matches only tmp
        tmp_hit = match_all(rs, "/a/tmp/x", "x", is_dir=False, raw_path="/a/tmp/x")
        assert len(tmp_hit) == 1 and tmp_hit[0].name == "tmp"

        # A path that matches only cache
        cache_hit = match_all(rs, "/a/.cache/x", "x", is_dir=False, raw_path="/a/.cache/x")
        assert len(cache_hit) == 1 and cache_hit[0].name == "cache"

    def test_alt_matches_directory_entry_itself(self) -> None:
        """Alt key fires when the path *is* the segment (no trailing child)."""
        rs = compile_ruleset([[_rule("r", "**/node_modules/**")]])
        result = match_all(
            rs,
            "/a/node_modules",
            "node_modules",
            is_dir=True,
            raw_path="/a/node_modules",
        )
        assert len(result) == 1

    def test_alt_does_not_fire_as_substring(self) -> None:
        """Alt /tmp must not fire on /a/tmp_old (no slash boundary)."""
        rs = compile_ruleset([[_rule("r", "**/tmp/**")]])
        # /a/tmp_old — the alt "/tmp" appears at position 5 but is followed
        # by "_old", so it doesn't end the path → no match.
        # The val "/tmp/" also doesn't appear.
        result = match_all(rs, "/a/tmp_old", "tmp_old", is_dir=True, raw_path="/a/tmp_old")
        assert result == []

    def test_contains_multi_segment(self) -> None:
        """Multi-segment CONTAINS like **/cache/pip/** works."""
        rs = compile_ruleset([[_rule("r", "**/.cache/pip/**")]])
        result = match_all(
            rs,
            "/home/user/.cache/pip/wheels/x",
            "x",
            is_dir=False,
            raw_path="/home/user/.cache/pip/wheels/x",
        )
        assert len(result) == 1

    def test_contains_case_insensitive(self) -> None:
        rs = compile_ruleset([[_rule("r", "**/TMP/**")]])
        result = match_all(rs, "/a/tmp/b", "b", is_dir=False, raw_path="/a/TMP/b")
        assert len(result) == 1

    def test_contains_dedup_by_category(self) -> None:
        rs = compile_ruleset(
            [
                [
                    _rule("first", "**/tmp/**", InsightCategory.TEMP),
                    _rule("second", "**/tmp/**", InsightCategory.TEMP),
                ]
            ]
        )
        result = match_all(rs, "/a/tmp/b", "b", is_dir=False, raw_path="/a/tmp/b")
        assert len(result) == 1
        assert result[0].name == "first"


def test_startswith_match() -> None:
    rs = compile_ruleset([[_rule("r", "**/npm-debug.log*", apply_to="file")]])
    result = match_all(rs, "/a/npm-debug.log.1", "npm-debug.log.1", is_dir=False, raw_path="/a/npm-debug.log.1")
    assert len(result) == 1


def test_glob_fallback() -> None:
    rs = compile_ruleset(
        [
            [
                _rule("r", "**/Library/Application Support/CrashReporter/**"),
            ]
        ]
    )
    # This pattern has a space in the middle segment — _classify should see
    # no glob chars in the middle so it's CONTAINS. Let's verify it matches.
    result = match_all(
        rs,
        "/users/x/library/application support/crashreporter/foo",
        "foo",
        is_dir=False,
        raw_path="/Users/x/Library/Application Support/CrashReporter/foo",
    )
    assert len(result) == 1


def test_ac_fields_none_when_no_ac_rules() -> None:
    """for_file.ac/for_dir.ac are None when no CONTAINS/ENDSWITH rules for that apply_to."""
    rs = compile_ruleset([[_rule("r", "**/.DS_Store", apply_to="file")]])
    assert rs.for_file.ac is None
    assert rs.for_dir.ac is None


def test_ac_both_populated_for_both_contains() -> None:
    rs = compile_ruleset([[_rule("r", "**/tmp/**", apply_to="both")]])
    assert rs.for_file.ac is not None
    assert rs.for_dir.ac is not None


def test_additional_paths_exact_match() -> None:
    rule = _rule("extra", "**/*", InsightCategory.CACHE)
    rs = compile_ruleset([], additional_paths=[("/home/user/.cache", rule)])
    result = match_all(rs, "/home/user/.cache", ".cache", is_dir=True, raw_path="/home/user/.cache")
    assert len(result) == 1


def test_additional_paths_prefix_match() -> None:
    rule = _rule("extra", "**/*", InsightCategory.CACHE)
    rs = compile_ruleset([], additional_paths=[("/home/user/.cache", rule)])
    result = match_all(rs, "/home/user/.cache/pip/foo", "foo", is_dir=False, raw_path="/home/user/.cache/pip/foo")
    assert len(result) == 1


def test_additional_paths_no_partial_prefix() -> None:
    rule = _rule("extra", "**/*", InsightCategory.CACHE)
    rs = compile_ruleset([], additional_paths=[("/home/user/.cache", rule)])
    # ".cacheX" should NOT match "/home/user/.cache"
    result = match_all(rs, "/home/user/.cachex/foo", "foo", is_dir=False, raw_path="/home/user/.cacheX/foo")
    assert result == []


# ── Default rules integration ───────────────────────────────────────


@pytest.fixture()
def default_ruleset() -> CompiledRuleSet:
    from dux.config.defaults import default_config

    cfg = default_config()
    return compile_ruleset([cfg.temp_patterns, cfg.cache_patterns, cfg.build_artifact_patterns])


def _matches(rs: CompiledRuleSet, path: str, basename: str, is_dir: bool) -> list[PatternRule]:
    return match_all(rs, path.lower(), basename.lower(), is_dir, path)


class TestDefaultRulesTemp:
    def test_tmp_dir(self, default_ruleset: CompiledRuleSet) -> None:
        result = _matches(default_ruleset, "/a/tmp/b", "b", is_dir=False)
        assert any(r.category == InsightCategory.TEMP for r in result)

    def test_log_file(self, default_ruleset: CompiledRuleSet) -> None:
        result = _matches(default_ruleset, "/a/b/app.log", "app.log", is_dir=False)
        assert any(r.category == InsightCategory.TEMP for r in result)

    def test_ds_store(self, default_ruleset: CompiledRuleSet) -> None:
        result = _matches(default_ruleset, "/a/.DS_Store", ".DS_Store", is_dir=False)
        assert any(r.category == InsightCategory.TEMP for r in result)

    def test_pytest_cache(self, default_ruleset: CompiledRuleSet) -> None:
        result = _matches(default_ruleset, "/a/.pytest_cache/v/cache", "cache", is_dir=False)
        assert any(r.category == InsightCategory.TEMP for r in result)

    def test_coverage_files(self, default_ruleset: CompiledRuleSet) -> None:
        result = _matches(default_ruleset, "/a/.coverage.abc", ".coverage.abc", is_dir=False)
        assert any(r.category == InsightCategory.TEMP for r in result)

    def test_editor_swaps(self, default_ruleset: CompiledRuleSet) -> None:
        result = _matches(default_ruleset, "/a/file.swp", "file.swp", is_dir=False)
        assert any(r.category == InsightCategory.TEMP for r in result)

    def test_mypy_cache(self, default_ruleset: CompiledRuleSet) -> None:
        result = _matches(default_ruleset, "/a/.mypy_cache/x", "x", is_dir=False)
        assert any(r.category == InsightCategory.TEMP for r in result)

    def test_ruff_cache(self, default_ruleset: CompiledRuleSet) -> None:
        result = _matches(default_ruleset, "/a/.ruff_cache/x", "x", is_dir=False)
        assert any(r.category == InsightCategory.TEMP for r in result)


class TestDefaultRulesCache:
    def test_npm_cache(self, default_ruleset: CompiledRuleSet) -> None:
        result = _matches(default_ruleset, "/a/.npm/foo", "foo", is_dir=False)
        assert any(r.category == InsightCategory.CACHE for r in result)

    def test_pip_cache(self, default_ruleset: CompiledRuleSet) -> None:
        result = _matches(default_ruleset, "/a/.cache/pip/foo", "foo", is_dir=False)
        assert any(r.category == InsightCategory.CACHE for r in result)

    def test_gradle_cache(self, default_ruleset: CompiledRuleSet) -> None:
        result = _matches(default_ruleset, "/a/.gradle/caches/foo", "foo", is_dir=False)
        assert any(r.category == InsightCategory.CACHE for r in result)

    def test_cargo_registry(self, default_ruleset: CompiledRuleSet) -> None:
        result = _matches(default_ruleset, "/a/.cargo/registry/foo", "foo", is_dir=False)
        assert any(r.category == InsightCategory.CACHE for r in result)

    def test_huggingface_cache(self, default_ruleset: CompiledRuleSet) -> None:
        result = _matches(default_ruleset, "/a/.cache/huggingface/models/x", "x", is_dir=False)
        assert any(r.category == InsightCategory.CACHE for r in result)


class TestDefaultRulesBuildArtifact:
    def test_node_modules(self, default_ruleset: CompiledRuleSet) -> None:
        result = _matches(default_ruleset, "/a/node_modules/foo", "foo", is_dir=False)
        assert any(r.category == InsightCategory.BUILD_ARTIFACT for r in result)

    def test_venv(self, default_ruleset: CompiledRuleSet) -> None:
        result = _matches(default_ruleset, "/a/.venv/lib/foo", "foo", is_dir=False)
        assert any(r.category == InsightCategory.BUILD_ARTIFACT for r in result)

    def test_pycache(self, default_ruleset: CompiledRuleSet) -> None:
        result = _matches(default_ruleset, "/a/__pycache__/foo.pyc", "foo.pyc", is_dir=False)
        assert any(r.category == InsightCategory.BUILD_ARTIFACT for r in result)

    def test_egg_info_dir(self, default_ruleset: CompiledRuleSet) -> None:
        result = _matches(default_ruleset, "/a/foo.egg-info", "foo.egg-info", is_dir=True)
        assert any(r.category == InsightCategory.BUILD_ARTIFACT for r in result)

    def test_egg_info_not_file(self, default_ruleset: CompiledRuleSet) -> None:
        """egg-info rule has apply_to='dir', should not match files."""
        result = _matches(default_ruleset, "/a/foo.egg-info", "foo.egg-info", is_dir=False)
        ba_rules = [r for r in result if r.name == "Python Egg Info"]
        assert ba_rules == []

    def test_tox(self, default_ruleset: CompiledRuleSet) -> None:
        result = _matches(default_ruleset, "/a/.tox/py39/lib/foo", "foo", is_dir=False)
        assert any(r.category == InsightCategory.BUILD_ARTIFACT for r in result)

    def test_rust_target(self, default_ruleset: CompiledRuleSet) -> None:
        result = _matches(default_ruleset, "/a/target/release/bin", "bin", is_dir=False)
        assert any(r.category == InsightCategory.BUILD_ARTIFACT for r in result)


def test_case_insensitive_through_pipeline(default_ruleset: CompiledRuleSet) -> None:
    """Uppercase paths match when lowercased before match_all."""
    path = "/A/NODE_MODULES/foo"
    result = match_all(default_ruleset, path.lower(), "foo", is_dir=False, raw_path=path)
    assert any(r.category == InsightCategory.BUILD_ARTIFACT for r in result)
