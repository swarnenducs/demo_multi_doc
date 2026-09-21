"""No emoji in source files or print/echo/Write-Host lines."""

from __future__ import annotations

from pathlib import Path

_EMOJI_RANGES = (
    (0x1F300, 0x1F5FF),
    (0x1F600, 0x1F64F),
    (0x1F680, 0x1F6FF),
    (0x1F700, 0x1FAFF),
    (0x1F1E6, 0x1F1FF),
    (0x2600, 0x26FF),
    (0x2700, 0x27BF),
    (0xFE0F, 0xFE0F),
)
_CODE_SUFFIXES = {".py", ".ps1", ".sh", ".js", ".ts", ".tsx", ".jsx"}
_SKIP_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    "dist",
    "build",
    "data",
}


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "run_all_components.py").is_file():
            return parent
    raise RuntimeError("repo root not found")


def _is_emoji(char: str) -> bool:
    code = ord(char)
    return any(lo <= code <= hi for lo, hi in _EMOJI_RANGES)


def _iter_code_files() -> list[Path]:
    root = _repo_root()
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in _CODE_SUFFIXES:
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        files.append(path)
    return files


def test_no_emoji_in_code_or_prints() -> None:
    root = _repo_root()
    code_hits: list[str] = []
    print_hits: list[str] = []
    for path in _iter_code_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rel = path.relative_to(root)
        for lineno, line in enumerate(text.splitlines(), 1):
            found = [ch for ch in line if _is_emoji(ch)]
            if not found:
                continue
            loc = f"{rel}:{lineno} {''.join(dict.fromkeys(found))}"
            code_hits.append(loc)
            if "print(" in line or "echo " in line or "Write-Host" in line:
                print_hits.append(loc)
    assert not print_hits, "emoji in print/echo:\n" + "\n".join(print_hits[:40])
    assert not code_hits, "emoji in source:\n" + "\n".join(code_hits[:40])
