"""Read a repository from disk and describe it, deterministically.

No model, no network, no heuristics that change between runs: the same tree must produce the
same profile, byte for byte, or the profile is not something a verdict can point at. The
detector is also deliberately shallow — it reads manifests and file names, not source code.
Understanding the architecture is a later phase and, unlike this file, it involves a model.
"""

from __future__ import annotations

import json
import tomllib
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .profile import ProfileTool, ProjectProfile

MAX_FILES = 50_000
"""A guard against being pointed at a filesystem; a real repository is far below this."""

IGNORED_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "env",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        "dist",
        "build",
        "target",
        ".idea",
        ".vscode",
        ".qagent",
    }
)

LANGUAGE_BY_SUFFIX = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".swift": "swift",
    ".sql": "sql",
    ".sh": "shell",
    ".bash": "shell",
    ".lua": "lua",
    ".scala": "scala",
    ".ex": "elixir",
    ".exs": "elixir",
}

TEST_DIRS = ("tests", "test", "spec", "__tests__")
DOC_SUFFIXES = (".md", ".rst")
ENTRY_FILES = ("main.py", "app.py", "manage.py", "cli.py", "server.py")

MAKE_TARGETS = frozenset(
    {"test", "tests", "lint", "check", "typecheck", "type-check", "types", "fmt"}
)


def detect_profile(root: Path) -> ProjectProfile:
    """Describe the repository at ``root``.

    Raises :class:`NotADirectoryError` when the path is not a directory — a run planned
    against a missing project is a bug, not an empty profile.
    """
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"not a directory: {root}")

    languages, file_count = _walk(root)
    pyproject = _read_toml(root / "pyproject.toml")
    package_json = _read_json(root / "package.json")
    tools = _tools(root, pyproject, package_json)

    return ProjectProfile(
        name=root.name,
        languages=dict(sorted(languages.items())),
        package_managers=_package_managers(root, pyproject),
        tools=sorted(tools, key=lambda tool: tool.name),
        test_paths=_test_paths(root),
        entry_points=_entry_points(root, pyproject, package_json),
        top_level=_top_level(root),
        docs=sorted(p.name for p in root.iterdir() if p.is_file() and p.suffix in DOC_SUFFIXES)
        if root.is_dir()
        else [],
        file_count=file_count,
        python_version=_python_version(pyproject),
    )


def _walk(root: Path) -> tuple[dict[str, int], int]:
    counts: Counter[str] = Counter()
    seen = 0
    for path in _iter_files(root):
        seen += 1
        if seen > MAX_FILES:
            break
        language = LANGUAGE_BY_SUFFIX.get(path.suffix.lower())
        if language:
            counts[language] += 1
    return dict(counts), min(seen, MAX_FILES)


def _iter_files(root: Path) -> Iterator[Path]:
    """Walk sorted, so two runs over the same tree visit files in the same order."""
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_symlink():
                continue
            if entry.is_dir():
                if entry.name not in IGNORED_DIRS:
                    stack.append(entry)
                continue
            if entry.name in IGNORED_DIRS:
                continue
            yield entry


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _tools(
    root: Path, pyproject: dict[str, Any], package_json: dict[str, Any]
) -> list[ProfileTool]:
    tools: list[ProfileTool] = []
    tools.extend(_python_tools(root, pyproject))
    tools.extend(_node_tools(root, package_json))
    tools.extend(_manifest_tools(root))
    tools.extend(_make_tools(root))
    return tools


def _python_tools(root: Path, pyproject: dict[str, Any]) -> list[ProfileTool]:
    tool_table = _table(pyproject, "tool")
    found: list[ProfileTool] = []

    def add(name: str, command: list[str], source: str) -> None:
        if all(tool.name != name for tool in found):
            found.append(ProfileTool(name=name, command=command, source=source))

    if "ruff" in tool_table or (root / "ruff.toml").exists() or (root / ".ruff.toml").exists():
        source = "pyproject.toml" if "ruff" in tool_table else "ruff.toml"
        add("ruff", ["ruff", "check", "."], source)
    if (
        "mypy" in tool_table
        or (root / "mypy.ini").exists()
        or (root / ".mypy.ini").exists()
        or "mypy" in _read_toml(root / "setup.cfg")
    ):
        add("mypy", ["mypy"], "pyproject.toml" if "mypy" in tool_table else "mypy.ini")
    if "black" in tool_table:
        add("black", ["black", "--check", "."], "pyproject.toml [tool.black]")
    if "pytest" in tool_table or _has_pytest_config(root) or _has_tests(root):
        add("pytest", ["python", "-m", "pytest"], _pytest_source(root, tool_table))
    if any((root / name).exists() for name in (".flake8", "setup.cfg", "tox.ini", "flake8.ini")):
        add("flake8", ["flake8"], "setup.cfg")
    return found


def _pytest_source(root: Path, tool_table: dict[str, Any]) -> str:
    if "pytest" in tool_table:
        return "pyproject.toml [tool.pytest]"
    for name in ("pytest.ini", "tox.ini", "setup.cfg"):
        if (root / name).exists():
            return name
    return "tests/ directory"


def _has_pytest_config(root: Path) -> bool:
    return any((root / name).exists() for name in ("pytest.ini", "tox.ini", "setup.cfg"))


def _has_tests(root: Path) -> bool:
    for name in TEST_DIRS:
        directory = root / name
        if directory.is_dir() and any(directory.rglob("test_*.py")):
            return True
    return False


def _node_tools(root: Path, package_json: dict[str, Any]) -> list[ProfileTool]:
    if not package_json and not (root / "package.json").exists():
        return []
    dependencies = _node_dependencies(package_json)
    found: list[ProfileTool] = []

    def add(name: str, command: list[str], source: str) -> None:
        if all(tool.name != name for tool in found):
            found.append(ProfileTool(name=name, command=command, source=source))

    if "eslint" in dependencies:
        add("eslint", ["npx", "eslint", "."], "package.json devDependencies")
    if "typescript" in dependencies or (root / "tsconfig.json").exists():
        add("tsc", ["npx", "tsc", "--noEmit"], "package.json devDependencies")
    if "vitest" in dependencies:
        add("vitest", ["npx", "vitest", "run"], "package.json devDependencies")
    elif "jest" in dependencies:
        add("jest", ["npx", "jest"], "package.json devDependencies")
    scripts = package_json.get("scripts")
    if isinstance(scripts, dict) and "test" in scripts and not any(
        tool.name in {"jest", "vitest"} for tool in found
    ):
        add("npm-test", ["npm", "run", "test"], "package.json scripts.test")
    return found


def _node_dependencies(package_json: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for key in ("dependencies", "devDependencies"):
        group = package_json.get(key)
        if isinstance(group, dict):
            names.update(str(name) for name in group)
    return names


def _manifest_tools(root: Path) -> list[ProfileTool]:
    found: list[ProfileTool] = []
    if (root / "go.mod").exists():
        found.append(ProfileTool(name="go-test", command=["go", "test", "./..."], source="go.mod"))
    if (root / "Cargo.toml").exists():
        found.append(ProfileTool(name="cargo-test", command=["cargo", "test"], source="Cargo.toml"))
    return found


def _make_tools(root: Path) -> list[ProfileTool]:
    makefile = root / "Makefile"
    if not makefile.is_file():
        return []
    try:
        lines = makefile.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    targets = []
    for line in lines:
        if line[:1].isspace() or line.startswith("#") or ":" not in line:
            continue
        name = line.split(":", 1)[0].strip()
        if name in MAKE_TARGETS:
            targets.append(name)
    return [
        ProfileTool(name=f"make-{name}", command=["make", name], source="Makefile")
        for name in sorted(set(targets))
    ]


def _package_managers(root: Path, pyproject: dict[str, Any]) -> list[str]:
    managers: list[str] = []
    if pyproject:
        managers.append("poetry" if "poetry" in _table(pyproject, "tool") else "pip")
    if (root / "requirements.txt").exists() or (root / "setup.py").exists():
        managers.append("pip")
    if (root / "package.json").exists():
        if (root / "pnpm-lock.yaml").exists():
            managers.append("pnpm")
        elif (root / "yarn.lock").exists():
            managers.append("yarn")
        else:
            managers.append("npm")
    if (root / "go.mod").exists():
        managers.append("go")
    if (root / "Cargo.toml").exists():
        managers.append("cargo")
    return sorted(set(managers))


def _test_paths(root: Path) -> list[str]:
    found = [name for name in TEST_DIRS if (root / name).is_dir()]
    if (root / "src").is_dir():
        found.extend(f"src/{name}" for name in TEST_DIRS if (root / "src" / name).is_dir())
    return sorted(found)


def _entry_points(root: Path, pyproject: dict[str, Any], package_json: dict[str, Any]) -> list[str]:
    points: list[str] = []
    scripts = _table(_table(pyproject, "project"), "scripts")
    points.extend(f"script:{name}" for name in scripts)
    bins = package_json.get("bin")
    if isinstance(bins, dict):
        points.extend(f"bin:{name}" for name in bins)
    elif isinstance(bins, str):
        points.append("bin:main")
    for name in ENTRY_FILES:
        if (root / name).is_file():
            points.append(name)
    points.extend(
        str(path.relative_to(root))
        for path in sorted(root.glob("src/*/__main__.py"))
    )
    if (root / "__main__.py").is_file():
        points.append("__main__.py")
    return sorted(set(points))


def _top_level(root: Path) -> list[str]:
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return []
    return [entry.name for entry in entries if entry.is_dir() and entry.name not in IGNORED_DIRS]


def _table(parent: dict[str, Any], key: str) -> dict[str, Any]:
    """A TOML/JSON sub-table, or an empty one when the key is absent or not a table."""
    value = parent.get(key)
    return value if isinstance(value, dict) else {}


def _python_version(pyproject: dict[str, Any]) -> str | None:
    declared = _table(pyproject, "project").get("requires-python")
    if isinstance(declared, str):
        return declared
    version = _table(_table(pyproject, "tool"), "poetry").get("python")
    return version if isinstance(version, str) else None
