"""Mechanical rules that review used to catch. Standard library only, so it runs without a project environment.

  check_rules.py                 check every tracked file
  check_rules.py FILE...         check these files (what pre-commit passes)
  check_rules.py commit-msg FILE reject a Co-Authored-By trailer that names an AI
  check_rules.py --self-check    prove each rule fails on a bad input and passes on a good one

The rules:
- register_function: no call to it, or to register_direct_function, under agent/src. A handler added that way
  is never dropped, so it runs in every node whatever the node offers. That bypasses tool gating (ADR 0003).
- dashes: no em or en dash in a Markdown file, in a code comment or in a Python docstring.
- commit-msg: no Co-Authored-By trailer naming an AI.

Exit status is 1 when any rule fails, with one `path:line: message` per finding.
"""

from __future__ import annotations

import ast
import contextlib
import io
import re
import subprocess
import sys
import tempfile
import tokenize
from collections.abc import Iterator
from pathlib import Path, PurePosixPath

EM_DASH = "—"
EN_DASH = "–"
DASHES = (EM_DASH, EN_DASH)

REGISTRATION_CALLS = {"register_function", "register_direct_function"}
GATED_SOURCE = PurePosixPath("agent/src")

# A trailer such as `Co-Authored-By: Claude <noreply@anthropic.com>` names an AI when its value has one of these.
AI_NAMES = re.compile(
    r"\b(claude|anthropic|sonnet|opus|haiku|codex|openai|chatgpt|gpt|copilot|gemini|cursor|aider|devin|windsurf|ai)\b",
    re.IGNORECASE,
)
CO_AUTHOR_TRAILER = re.compile(r"^\s*co-authored-by\s*:(.*)$", re.IGNORECASE)

HASH_COMMENT_SUFFIXES = {".sh", ".bash", ".yml", ".yaml", ".toml"}
SLASH_COMMENT_SUFFIXES = {".ts", ".tsx", ".js", ".mjs", ".cjs"}
LOCKFILES = {"uv.lock", "pnpm-lock.yaml"}

Finding = tuple[int, str]  # line number, message


def dashes_in_text(text: str) -> Iterator[Finding]:
    for number, line in enumerate(text.splitlines(), start=1):
        if any(dash in line for dash in DASHES):
            yield number, "em or en dash; use a period or a comma"


def python_comments(source: str) -> Iterator[tuple[int, str]]:
    """Every comment, plus every docstring line, as (line number, text)."""
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.COMMENT:
                yield token.start[0], token.string
        tree = ast.parse(source)
    except (SyntaxError, tokenize.TokenError):
        return  # ruff reports a file that does not parse
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            doc = node.body[0] if node.body else None
            if isinstance(doc, ast.Expr) and isinstance(doc.value, ast.Constant) and isinstance(doc.value.value, str):
                for offset, line in enumerate(doc.value.value.splitlines()):
                    yield doc.lineno + offset, line


def slash_comments(source: str) -> Iterator[tuple[int, str]]:
    """Comments of a C-like language, skipping string and template literals."""
    i, line, n = 0, 1, len(source)
    while i < n:
        char = source[i]
        if char == "\n":
            line += 1
            i += 1
        elif source.startswith("//", i):
            end = source.find("\n", i)
            end = n if end == -1 else end
            yield line, source[i:end]
            i = end
        elif source.startswith("/*", i):
            end = source.find("*/", i + 2)
            end = n if end == -1 else end + 2
            for offset, text in enumerate(source[i:end].split("\n")):
                yield line + offset, text
            line += source.count("\n", i, end)
            i = end
        elif char in "'\"`":
            i += 1
            while i < n and source[i] != char:
                if source[i] == "\\":
                    i += 1
                if i < n and source[i] == "\n":
                    line += 1
                    if char != "`":
                        break
                i += 1
            i += 1
        else:
            i += 1


def hash_comments(source: str) -> Iterator[tuple[int, str]]:
    """A `#` that starts a line or follows whitespace. A quoted ` #` with a dash after it is a false positive."""
    for number, text in enumerate(source.splitlines(), start=1):
        match = re.search(r"(^|\s)#", text)
        if match:
            yield number, text[match.start() :]


def comments_of(path: PurePosixPath, source: str) -> Iterator[tuple[int, str]]:
    suffix = path.suffix
    if suffix == ".py":
        return python_comments(source)
    if suffix in SLASH_COMMENT_SUFFIXES:
        return slash_comments(source)
    if suffix in HASH_COMMENT_SUFFIXES or source.startswith(("#!/usr/bin/env bash", "#!/bin/bash")):
        return hash_comments(source)
    return iter(())


def dash_findings(path: PurePosixPath, source: str) -> list[Finding]:
    if path.suffix == ".md":
        return list(dashes_in_text(source))
    return [
        (number, "em or en dash in a comment; use a period or a comma")
        for number, text in comments_of(path, source)
        if any(dash in text for dash in DASHES)
    ]


def registration_findings(path: PurePosixPath, source: str) -> list[Finding]:
    if path.suffix != ".py" or not path.is_relative_to(GATED_SOURCE):
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
            if name in REGISTRATION_CALLS:
                found.append((node.lineno, f"{name} bypasses tool gating; offer the tool from a node (ADR 0003)"))
    return found


def check_source(path: str, source: str) -> list[Finding]:
    """Every finding of every file rule for one file, given its repo-relative POSIX path."""
    posix = PurePosixPath(path)
    return sorted(dash_findings(posix, source) + registration_findings(posix, source))


def commit_message_findings(message: str) -> list[Finding]:
    found = []
    for number, line in enumerate(message.splitlines(), start=1):
        if line.startswith("#"):  # git strips these
            continue
        trailer = CO_AUTHOR_TRAILER.match(line)
        if trailer and AI_NAMES.search(trailer.group(1)):
            found.append((number, "a Co-Authored-By trailer names an AI; remove it"))
    return found


def tracked_files(root: Path) -> list[str]:
    listing = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True, text=True, encoding="utf-8"
    )
    return [name for name in listing.stdout.split("\0") if name]


def check_files(root: Path, names: list[str]) -> int:
    failures = 0
    for name in names:
        path = Path(name)
        if path.name in LOCKFILES:
            continue
        absolute = path if path.is_absolute() else root / path
        try:
            source = absolute.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue  # binary, deleted or a submodule
        relative = absolute.resolve().relative_to(root.resolve()).as_posix()
        for number, message in check_source(relative, source):
            print(f"{relative}:{number}: {message}")
            failures += 1
    return failures


def self_check() -> int:
    """Each rule must fail on a bad input and pass on a good one. Inputs are in memory; none touches the repo."""
    cases: list[tuple[str, str, str, bool]] = [
        # (what it shows, path, source, expected to fail)
        ("register_function method call", "agent/src/clinic_agent/x.py", "llm.register_function('f', h)\n", True),
        ("register_function bare call", "agent/src/clinic_agent/x.py", "register_function('f', h)\n", True),
        ("register_direct_function", "agent/src/clinic_agent/x.py", "llm.register_direct_function(f)\n", True),
        (
            "register_function in a comment is fine",
            "agent/src/clinic_agent/x.py",
            "# register_function is banned\n",
            False,
        ),
        ("register_function outside agent/src is fine", "agent/tests/x.py", "llm.register_function('f', h)\n", False),
        ("em dash in Markdown", "docs/a.md", f"One {EM_DASH} two\n", True),
        ("en dash in Markdown", "docs/a.md", f"1{EN_DASH}2\n", True),
        ("plain Markdown", "docs/a.md", "One, two. A well-known hyphen.\n", False),
        ("em dash in a Python comment", "agent/src/x.py", f"x = 1  # one {EM_DASH} two\n", True),
        ("em dash in a Python docstring", "agent/src/x.py", f'def f():\n    """One {EM_DASH} two."""\n', True),
        ("em dash in a Python string", "agent/src/x.py", f'x = "one {EM_DASH} two"\n', False),
        ("em dash in a TypeScript line comment", "adapter/src/x.ts", f"const a = 1; // one {EM_DASH} two\n", True),
        ("em dash in a TypeScript block comment", "adapter/src/x.ts", f"/* one\n {EM_DASH} two */\n", True),
        ("em dash in a TypeScript string", "adapter/src/x.ts", f'const a = "one {EM_DASH} two";\n', False),
        ("em dash in a shell comment", "scripts/x.sh", f"# one {EM_DASH} two\n", True),
        ("em dash in a YAML comment", ".github/x.yml", f"a: 1 # one {EM_DASH} two\n", True),
    ]
    failed = 0
    for what, path, source, should_fail in cases:
        did_fail = bool(check_source(path, source))
        if did_fail != should_fail:
            print(f"self-check FAILED: {what}: expected {'a finding' if should_fail else 'no finding'}")
            failed += 1

    messages = [
        ("Claude trailer", "Add a thing\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n", True),
        ("lowercase Codex trailer", "Add a thing\n\nco-authored-by: Codex\n", True),
        ("Copilot trailer", "Add a thing\n\nCo-authored-by: GitHub Copilot <copilot@github.com>\n", True),
        ("human co-author", "Add a thing\n\nCo-Authored-By: Ada Lovelace <ada@example.com>\n", False),
        ("no trailer", "Add a thing\n\nBody mentioning Claude in prose.\n", False),
        ("commented-out trailer", "Add a thing\n\n# Co-Authored-By: Claude\n", False),
    ]
    for what, message, should_fail in messages:
        did_fail = bool(commit_message_findings(message))
        if did_fail != should_fail:
            print(f"self-check FAILED: commit-msg {what}: expected {'a finding' if should_fail else 'no finding'}")
            failed += 1

    # The same path a run takes: real files on disk, counted into the exit status.
    with tempfile.TemporaryDirectory() as scratch:
        tree = Path(scratch)
        (tree / "agent" / "src").mkdir(parents=True)
        (tree / "bad.md").write_text(f"One {EM_DASH} two\n", encoding="utf-8")
        (tree / "agent" / "src" / "bad.py").write_text("llm.register_function('f', h)\n", encoding="utf-8")
        (tree / "good.md").write_text("One, two.\n", encoding="utf-8")
        for name, expected in [("bad.md", 1), ("agent/src/bad.py", 1), ("good.md", 0)]:
            with contextlib.redirect_stdout(io.StringIO()):
                found = check_files(tree, [name])
            if found != expected:
                print(f"self-check FAILED: {name} on disk: expected {expected} finding(s)")
                failed += 1

    if failed:
        return 1
    print(f"self-check: {len(cases) + len(messages) + 3} cases behave as expected")
    return 0


def main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parent.parent
    if argv[:1] == ["--self-check"]:
        return self_check()
    if argv[:1] == ["commit-msg"]:
        if len(argv) != 2:
            print("usage: check_rules.py commit-msg FILE", file=sys.stderr)
            return 2
        message = Path(argv[1]).read_text(encoding="utf-8")
        found = commit_message_findings(message)
        for number, text in found:
            print(f"commit message:{number}: {text}")
        return 1 if found else 0
    names = argv or tracked_files(root)
    return 1 if check_files(root, names) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
