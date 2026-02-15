from __future__ import annotations

from pathlib import Path

from diskanalysis.config.schema import AppConfig, PatternRule
from diskanalysis.models.enums import InsightCategory


def default_config() -> AppConfig:
    home = str(Path.home())

    temp_patterns = [
        PatternRule(
            "System Temp",
            "**/tmp/**",
            InsightCategory.TEMP,
        ),
        PatternRule(
            "User Temp",
            "**/.tmp/**",
            InsightCategory.TEMP,
        ),
        PatternRule(
            "Log Files",
            "**/*.log",
            InsightCategory.TEMP,
            "file",
        ),
        PatternRule(
            "Python Bytecode",
            "**/__pycache__/**",
            InsightCategory.TEMP,
        ),
        PatternRule(
            "Pytest Cache",
            "**/.pytest_cache/**",
            InsightCategory.TEMP,
        ),
        PatternRule(
            "Mypy Cache",
            "**/.mypy_cache/**",
            InsightCategory.TEMP,
        ),
        PatternRule(
            "Ruff Cache",
            "**/.ruff_cache/**",
            InsightCategory.TEMP,
        ),
        PatternRule(
            "Coverage Files",
            "**/.coverage*",
            InsightCategory.TEMP,
        ),
        PatternRule(
            "Editor Swaps",
            "**/*.{swp,swo,tmp,bak}",
            InsightCategory.TEMP,
            "file",
        ),
        PatternRule(
            "macOS Metadata",
            "**/.DS_Store",
            InsightCategory.TEMP,
            "file",
        ),
        PatternRule(
            "npm Logs",
            "**/npm-debug.log*",
            InsightCategory.TEMP,
            "file",
        ),
        PatternRule(
            "Yarn Logs",
            "**/yarn-error.log*",
            InsightCategory.TEMP,
            "file",
        ),
        PatternRule(
            "Crash Reports",
            "**/Library/Application Support/CrashReporter/**",
            InsightCategory.TEMP,
        ),
        PatternRule(
            "SQLite Journals",
            "**/*.db-journal",
            InsightCategory.TEMP,
            "file",
        ),
    ]

    cache_patterns = [
        # ── Package managers ──
        PatternRule(
            "npm Cache",
            "**/.npm/**",
            InsightCategory.CACHE,
        ),
        PatternRule(
            "Yarn Cache",
            "**/.cache/yarn/**",
            InsightCategory.CACHE,
        ),
        PatternRule(
            "pnpm Store",
            "**/.pnpm-store/**",
            InsightCategory.CACHE,
        ),
        PatternRule(
            "pip Cache",
            "**/.cache/pip/**",
            InsightCategory.CACHE,
        ),
        PatternRule(
            "uv Cache",
            "**/.cache/uv/**",
            InsightCategory.CACHE,
        ),
        PatternRule(
            "poetry Cache",
            "**/.cache/pypoetry/**",
            InsightCategory.CACHE,
        ),
        PatternRule(
            "conda Packages",
            "**/.conda/pkgs/**",
            InsightCategory.CACHE,
        ),
        PatternRule(
            "NuGet Cache",
            "**/.nuget/packages/**",
            InsightCategory.CACHE,
        ),
        PatternRule(
            "Composer Cache",
            "**/.composer/cache/**",
            InsightCategory.CACHE,
        ),
        PatternRule(
            "Bundler Cache",
            "**/.bundle/cache/**",
            InsightCategory.CACHE,
        ),
        # ── JVM ecosystem ──
        PatternRule(
            "Gradle Cache",
            "**/.gradle/**",
            InsightCategory.CACHE,
        ),
        PatternRule(
            "Maven Repo",
            "**/.m2/repository/**",
            InsightCategory.CACHE,
        ),
        PatternRule(
            "Ivy Cache",
            "**/.ivy2/cache/**",
            InsightCategory.CACHE,
        ),
        PatternRule(
            "SBT Boot",
            "**/.sbt/**",
            InsightCategory.CACHE,
        ),
        PatternRule(
            "Coursier Cache",
            "**/.cache/coursier/**",
            InsightCategory.CACHE,
        ),
        # ── Rust / Go / Native ──
        PatternRule(
            "Cargo Registry",
            "**/.cargo/registry/**",
            InsightCategory.CACHE,
        ),
        PatternRule(
            "rustup Downloads",
            "**/.rustup/downloads/**",
            InsightCategory.CACHE,
        ),
        PatternRule(
            "Go Module Cache",
            "**/go/pkg/mod/**",
            InsightCategory.CACHE,
        ),
        PatternRule(
            "Go Build Cache",
            "**/.cache/go-build/**",
            InsightCategory.CACHE,
        ),
        # ── JS build tool caches ──
        PatternRule(
            "Turbo Cache",
            "**/.turbo/**",
            InsightCategory.CACHE,
        ),
        PatternRule(
            "Parcel Cache",
            "**/.parcel-cache/**",
            InsightCategory.CACHE,
        ),
        PatternRule(
            "Node GYP",
            "**/.node-gyp/**",
            InsightCategory.CACHE,
        ),
        # ── Containers / Infra ──
        PatternRule(
            "Kube Cache",
            "**/.kube/cache/**",
            InsightCategory.CACHE,
        ),
        PatternRule(
            "Ansible Temp",
            "**/.ansible/tmp/**",
            InsightCategory.CACHE,
        ),
        # ── AI / ML model caches ──
        PatternRule(
            "HuggingFace Cache",
            "**/.cache/huggingface/**",
            InsightCategory.CACHE,
        ),
        PatternRule(
            "PyTorch Cache",
            "**/.cache/torch/**",
            InsightCategory.CACHE,
        ),
        PatternRule(
            "Whisper Cache",
            "**/.cache/whisper/**",
            InsightCategory.CACHE,
        ),
        PatternRule(
            "Ollama Models",
            "**/.ollama/**",
            InsightCategory.CACHE,
        ),
        # ── Linters / pre-commit ──
        PatternRule(
            "pre-commit Cache",
            "**/.cache/pre-commit/**",
            InsightCategory.CACHE,
        ),
    ]

    build_artifact_patterns = [
        # ── JS / Node ──
        PatternRule(
            "node_modules",
            "**/node_modules/**",
            InsightCategory.BUILD_ARTIFACT,
            stop_recursion=True,
        ),
        PatternRule(
            "Bower Components",
            "**/bower_components/**",
            InsightCategory.BUILD_ARTIFACT,
            stop_recursion=True,
        ),
        PatternRule(
            "Next.js build",
            "**/.next/**",
            InsightCategory.BUILD_ARTIFACT,
            stop_recursion=True,
        ),
        PatternRule(
            "Nuxt build",
            "**/.nuxt/**",
            InsightCategory.BUILD_ARTIFACT,
            stop_recursion=True,
        ),
        # ── Python ──
        PatternRule(
            "Python venv",
            "**/.venv/**",
            InsightCategory.BUILD_ARTIFACT,
            stop_recursion=True,
        ),
        PatternRule(
            "Python venv",
            "**/venv/**",
            InsightCategory.BUILD_ARTIFACT,
            stop_recursion=True,
        ),
        PatternRule(
            "Python cache",
            "**/__pycache__/**",
            InsightCategory.BUILD_ARTIFACT,
            stop_recursion=True,
        ),
        PatternRule(
            "Python Egg Info",
            "**/*.egg-info",
            InsightCategory.BUILD_ARTIFACT,
            apply_to="dir",
            stop_recursion=True,
        ),
        PatternRule(
            "tox env",
            "**/.tox/**",
            InsightCategory.BUILD_ARTIFACT,
            stop_recursion=True,
        ),
        # ── Generic build outputs ──
        PatternRule(
            "Build dir",
            "**/build/**",
            InsightCategory.BUILD_ARTIFACT,
            stop_recursion=True,
        ),
        PatternRule(
            "Dist dir",
            "**/dist/**",
            InsightCategory.BUILD_ARTIFACT,
            stop_recursion=True,
        ),
        PatternRule(
            "Object files",
            "**/obj/**",
            InsightCategory.BUILD_ARTIFACT,
            stop_recursion=True,
        ),
        PatternRule(
            "Coverage artifacts",
            "**/coverage/**",
            InsightCategory.BUILD_ARTIFACT,
            stop_recursion=True,
        ),
        # ── Native / compiled languages ──
        PatternRule(
            "Rust target",
            "**/target/**",
            InsightCategory.BUILD_ARTIFACT,
            stop_recursion=True,
        ),
        PatternRule(
            "Swift build",
            "**/.build/**",
            InsightCategory.BUILD_ARTIFACT,
            stop_recursion=True,
        ),
        PatternRule(
            "CMake build",
            "**/CMakeFiles/**",
            InsightCategory.BUILD_ARTIFACT,
            stop_recursion=True,
        ),
        PatternRule(
            "Zig cache",
            "**/zig-cache/**",
            InsightCategory.BUILD_ARTIFACT,
            stop_recursion=True,
        ),
    ]

    return AppConfig(
        additional_temp_paths=[],
        additional_cache_paths=[f"{home}/.cache"],
        temp_patterns=temp_patterns,
        cache_patterns=cache_patterns,
        build_artifact_patterns=build_artifact_patterns,
        max_depth=None,
        scan_workers=4,
        summary_top_count=15,
        page_size=100,
        max_insights_per_category=1000,
        overview_top_folders=100,
        scroll_step=20,
    )
