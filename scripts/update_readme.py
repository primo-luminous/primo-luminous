from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict

MARKER_START = "<!-- PROJECT_UPDATES:START -->"
MARKER_END = "<!-- PROJECT_UPDATES:END -->"
TABLE_HEADER = "| Project | Version | Updated |"
TABLE_DIVIDER = "| --- | --- | --- |"


def normalise_repo(repo_name: str, owner: str | None) -> tuple[str, str, str]:
    repo_name = repo_name.strip()
    if repo_name.startswith("http://") or repo_name.startswith("https://"):
        # Extract slug and display name from URL
        url = repo_name
        slug = repo_name.rstrip("/").split("github.com/")[-1]
        display = slug.split("/")[-1]
        return display, slug, url

    if "/" in repo_name:
        slug = repo_name
        display = repo_name.split("/")[-1]
    else:
        slug = f"{owner}/{repo_name}" if owner else repo_name
        display = repo_name
    url = f"https://github.com/{slug}"
    return display, slug, url


def parse_table(lines: List[str]) -> List[Dict[str, str]]:
    entries: List[Dict[str, str]] = []
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("|") or stripped in {TABLE_HEADER, TABLE_DIVIDER}:
            continue
        parts = [part.strip() for part in stripped.strip("|").split("|")]
        if len(parts) != 3:
            continue
        project_cell, version, updated = parts
        if project_cell.startswith("[") and "](" in project_cell:
            name_start = project_cell.find("[") + 1
            name_end = project_cell.find("]", name_start)
            url_start = project_cell.find("(", name_end) + 1
            url_end = project_cell.find(")", url_start)
            display = project_cell[name_start:name_end]
            url = project_cell[url_start:url_end]
        else:
            display = project_cell
            url = ""
        entries.append({
            "display": display,
            "version": version,
            "updated": updated,
            "url": url,
            "key": url or display.lower(),
        })
    return entries


def build_table(entries: List[Dict[str, str]]) -> List[str]:
    lines = [TABLE_HEADER, TABLE_DIVIDER]
    for entry in entries:
        project_cell = f"[{entry['display']}]({entry['url']})" if entry["url"] else entry["display"]
        lines.append(f"| {project_cell} | {entry['version']} | {entry['updated']} |")
    return lines


def ensure_markers(readme_lines: List[str]) -> List[str]:
    start_present = any(line.strip() == MARKER_START for line in readme_lines)
    end_present = any(line.strip() == MARKER_END for line in readme_lines)
    if start_present and end_present:
        return readme_lines

    insertion = [
        "\n",
        "## 🆕 Latest Repository Updates\n",
        "> _This section is automatically updated via GitHub Actions._\n",
        f"{MARKER_START}\n",
        f"{TABLE_HEADER}\n",
        f"{TABLE_DIVIDER}\n",
        f"{MARKER_END}\n",
    ]

    # Insert before the final signature if present, otherwise append.
    try:
        signature_index = next(
            i for i, line in enumerate(readme_lines) if line.startswith("⭐️")
        )
        return readme_lines[:signature_index] + insertion + readme_lines[signature_index:]
    except StopIteration:
        return readme_lines + ["\n"] + insertion


def find_marker_index(readme_lines: List[str], marker: str) -> int:
    for index, line in enumerate(readme_lines):
        if line.strip() == marker:
            return index
    raise ValueError(f"Marker {marker} not found in README")


def main() -> None:
    readme_path = Path(__file__).resolve().parents[1] / "README.md"
    if not readme_path.exists():
        raise FileNotFoundError("README.md not found")

    repo_name = os.getenv("REPO_NAME")
    if not repo_name:
        raise ValueError("REPO_NAME environment variable is required")

    owner = os.getenv("GITHUB_REPOSITORY_OWNER")
    display, slug, default_url = normalise_repo(repo_name, owner)

    version = os.getenv("REPO_VERSION", "unreleased").strip() or "unreleased"
    url = os.getenv("REPO_URL", "").strip() or default_url
    updated_at = os.getenv("REPO_UPDATED_AT", "").strip()
    if updated_at:
        try:
            # Normalise to ISO 8601 without timezone info if already provided
            parsed = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
            updated_iso = parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        except ValueError:
            updated_iso = updated_at
    else:
        updated_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    with readme_path.open("r", encoding="utf-8") as fh:
        readme_lines = fh.readlines()

    readme_lines = ensure_markers(readme_lines)

    start_index = find_marker_index(readme_lines, MARKER_START)
    end_index = find_marker_index(readme_lines, MARKER_END)

    table_lines = readme_lines[start_index + 1 : end_index]
    entries = parse_table(table_lines)

    new_key = url or f"https://github.com/{slug}"
    new_entry = {
        "display": display,
        "version": version,
        "updated": updated_iso,
        "url": new_key,
        "key": new_key,
    }

    filtered_entries = [entry for entry in entries if entry["key"].rstrip("/") != new_entry["key"].rstrip("/")]
    entries_to_write = [new_entry] + filtered_entries

    new_table = build_table(entries_to_write)

    updated_section = [line + "\n" for line in new_table]
    new_readme_lines = readme_lines[: start_index + 1] + updated_section + readme_lines[end_index:]

    if new_readme_lines != readme_lines:
        with readme_path.open("w", encoding="utf-8") as fh:
            fh.writelines(new_readme_lines)


if __name__ == "__main__":
    main()
