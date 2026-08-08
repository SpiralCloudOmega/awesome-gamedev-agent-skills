#!/usr/bin/env python3
"""Validator for awesome-gamedev-agent-skills.

Checks every ``skills/**/SKILL.md`` and ``router/SKILL.md`` against the authoring
standard in ``docs/SKILL-FORMAT.md``:

* valid YAML frontmatter delimited by ``---`` lines;
* ``name`` present, <=64 chars, lowercase ``a-z``/``0-9``/hyphens, no leading or
  trailing hyphen, equal to the containing folder name, and unique repo-wide;
* ``description`` present, non-empty, <=1024 chars;
* portable frontmatter (no reserved names, XML tags, or agent-specific keys);
* optional ``compatibility`` is non-empty and <=500 chars;
* the file is shorter than 500 lines;
* every internal ``references/`` link in the body resolves to a real file.

It also warns when ``description`` exceeds the 200-character claude.ai soft
limit or the experimental ``allowed-tools`` field is present.

Exits 0 when everything passes, 1 when any skill fails. No third-party deps.

Usage:
    python scripts/validate-skills.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_NAME = 64
MAX_DESCRIPTION = 1024
SOFT_MAX_DESCRIPTION = 200
MAX_COMPATIBILITY = 500
MAX_LINES = 500

RESERVED_NAME_WORDS = ("anthropic", "claude")
FORBIDDEN_KEYS = frozenset(
    {
        "when_to_use",
        "argument-hint",
        "disable-model-invocation",
        "user-invocable",
        "model",
        "paths",
        "hooks",
        "shell",
        "context",
        "globs",
        "alwaysApply",
        "trigger",
    }
)
# A negative lookbehind avoids treating attached generic syntax such as
# ``GetNode<T>`` as an XML tag while still rejecting standalone tags.
XML_TAG_RE = re.compile(r"(?<![\w.])</?[A-Za-z][A-Za-z0-9.-]*(?:\s[^<>]*)?/?>")

REPO_ROOT = Path(__file__).resolve().parent.parent

# Markdown links: [text](target)  and  inline-code paths: `references/foo.md`
MD_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
CODE_PATH_RE = re.compile(r"`([^`]*references/[^`]+?\.md)`")


def find_skill_files() -> list[Path]:
    files: list[Path] = []
    skills_dir = REPO_ROOT / "skills"
    if skills_dir.is_dir():
        files.extend(sorted(skills_dir.rglob("SKILL.md")))
    router = REPO_ROOT / "router" / "SKILL.md"
    if router.is_file():
        files.append(router)
    return files


def split_frontmatter(text: str) -> tuple[dict, int] | tuple[None, int]:
    """Return (frontmatter_dict, body_start_line) or (None, 0) if missing/malformed.

    Minimal YAML: supports ``key: value`` and a folded/literal block scalar
    (``key: >`` or ``key: |``) whose value is the following more-indented lines.
    Sufficient for the ``name`` and ``description`` fields we validate.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, 0

    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return None, 0

    fm: dict[str, str] = {}
    i = 1
    key_line_re = re.compile(r"^([A-Za-z0-9_-]+):\s*(.*)$")
    while i < end:
        raw = lines[i]
        if not raw.strip() or raw.lstrip().startswith("#"):
            i += 1
            continue
        # only treat top-level (non-indented) keys as fields
        if raw[:1] in (" ", "\t"):
            i += 1
            continue
        m = key_line_re.match(raw)
        if not m:
            i += 1
            continue
        key, inline = m.group(1), m.group(2).strip()
        if inline in (">", "|", ">-", "|-", ">+", "|+"):
            # block scalar: collect following indented lines
            block: list[str] = []
            j = i + 1
            while j < end:
                bl = lines[j]
                if bl.strip() == "":
                    block.append("")
                    j += 1
                    continue
                if bl[:1] in (" ", "\t"):
                    block.append(bl.strip())
                    j += 1
                else:
                    break
            fm[key] = " ".join(p for p in block if p != "").strip()
            i = j
        else:
            fm[key] = inline.strip().strip('"').strip("'")
            i += 1
    return fm, end + 1


def validate_file(path: Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    rel = path.relative_to(REPO_ROOT).as_posix()
    text = path.read_text(encoding="utf-8")

    # line count
    line_count = len(text.splitlines())
    if line_count >= MAX_LINES:
        errors.append(f"file is {line_count} lines (must be < {MAX_LINES})")

    fm, _ = split_frontmatter(text)
    if fm is None:
        errors.append("missing or malformed YAML frontmatter (need opening and closing '---')")
        return [f"{rel}: {e}" for e in errors], []

    for key in sorted(FORBIDDEN_KEYS.intersection(fm)):
        errors.append(f"forbidden frontmatter key: {key!r}")
    if "allowed-tools" in fm:
        warnings.append("experimental frontmatter key 'allowed-tools' reduces portability")

    # name
    name = fm.get("name", "")
    folder = path.parent.name
    if not name:
        errors.append("frontmatter 'name' is missing or empty")
    else:
        if len(name) > MAX_NAME:
            errors.append(f"'name' is {len(name)} chars (max {MAX_NAME})")
        if not NAME_RE.match(name):
            errors.append(
                f"'name' = {name!r} must be lowercase a-z/0-9/hyphens with no leading/trailing hyphen"
            )
        if any(word in name for word in RESERVED_NAME_WORDS):
            errors.append(f"'name' = {name!r} contains a reserved word")
        if XML_TAG_RE.search(name):
            errors.append(f"'name' = {name!r} must not contain XML tags")
        # router/SKILL.md lives in 'router'; skills live in their own folder
        if name != folder:
            errors.append(f"'name' = {name!r} must equal folder name {folder!r}")

    # description
    desc = fm.get("description", "")
    if not desc:
        errors.append("frontmatter 'description' is missing or empty")
    elif len(desc) > MAX_DESCRIPTION:
        errors.append(f"'description' is {len(desc)} chars (max {MAX_DESCRIPTION})")
    else:
        if len(desc) > SOFT_MAX_DESCRIPTION:
            warnings.append(
                f"'description' is {len(desc)} chars (soft target {SOFT_MAX_DESCRIPTION})"
            )
        if XML_TAG_RE.search(desc):
            errors.append("'description' must not contain XML tags")

    # optional compatibility
    if "compatibility" in fm:
        compatibility = fm["compatibility"]
        if not compatibility:
            errors.append("frontmatter 'compatibility' must not be empty when present")
        elif len(compatibility) > MAX_COMPATIBILITY:
            errors.append(
                f"'compatibility' is {len(compatibility)} chars (max {MAX_COMPATIBILITY})"
            )

    # internal references/ links resolve
    targets = set(MD_LINK_RE.findall(text)) | set(CODE_PATH_RE.findall(text))
    for target in targets:
        link = target.strip()
        # ignore external links and pure anchors
        if link.startswith(("http://", "https://", "#", "mailto:")):
            continue
        link = link.split("#", 1)[0].split("?", 1)[0]
        if not link:
            continue
        if "references/" not in link:
            continue
        resolved = (path.parent / link).resolve()
        if not resolved.exists():
            errors.append(f"reference link does not resolve: {target!r}")

    return (
        [f"{rel}: {e}" for e in errors],
        [f"{rel}: {warning}" for warning in warnings],
    )


def validate_unique_names(files: list[Path]) -> list[str]:
    """Return errors for names used by more than one skill directory."""
    paths_by_name: dict[str, list[Path]] = {}
    for path in files:
        fm, _ = split_frontmatter(path.read_text(encoding="utf-8"))
        if fm is None or not fm.get("name"):
            continue
        paths_by_name.setdefault(fm["name"], []).append(path)

    errors: list[str] = []
    for name, paths in sorted(paths_by_name.items()):
        if len(paths) < 2:
            continue
        locations = ", ".join(path.relative_to(REPO_ROOT).as_posix() for path in paths)
        errors.append(f"duplicate skill name {name!r}: {locations}")
    return errors


def main() -> int:
    files = find_skill_files()
    if not files:
        print("No SKILL.md files found yet (skills/** and router/ are empty). OK.")
        return 0

    all_errors: list[str] = []
    all_warnings: list[str] = []
    for path in files:
        errors, warnings = validate_file(path)
        all_errors.extend(errors)
        all_warnings.extend(warnings)
    all_errors.extend(validate_unique_names(files))

    print(f"Validated {len(files)} skill file(s).")
    if all_warnings:
        print(f"\nWARNINGS — {len(all_warnings)} portability warning(s):")
        for warning in all_warnings:
            print(f"  - {warning}")
    if all_errors:
        print(f"\nFAILED — {len(all_errors)} problem(s):")
        for e in all_errors:
            print(f"  - {e}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
