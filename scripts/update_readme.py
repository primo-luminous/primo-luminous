from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

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


def github_api_get(path: str, token: str | None) -> dict | list | None:
    url = path if path.startswith("http") else f"https://api.github.com/{path.lstrip('/')}"
    request = Request(url)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("User-Agent", "primo-luminous-readme-sync")
    if token:
        request.add_header("Authorization", f"Bearer {token}")

    proxy = os.getenv("https_proxy") or os.getenv("HTTPS_PROXY")
    if proxy:
        proxy = proxy.replace("http://", "").replace("https://", "")
        request.set_proxy(proxy, "https")

    try:
        with urlopen(request) as response:
            encoding = response.headers.get_content_charset("utf-8")
            payload = response.read().decode(encoding)
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise RuntimeError(f"GitHub API request failed with status {exc.code} for {url}") from exc
    except URLError as exc:  # pragma: no cover - network failure safety
        raise RuntimeError(f"Unable to reach GitHub API: {exc.reason}") from exc

    return json.loads(payload)


def fetch_repo_metadata(slug: str, token: str | None) -> Tuple[str | None, str | None]:
    repo = github_api_get(f"repos/{slug}", token)
    if not repo:
        raise RuntimeError(f"Repository '{slug}' was not found on GitHub")

    default_branch = repo.get("default_branch") or "main"
    version: str | None = None
    updated_at: str | None = None

    release = github_api_get(f"repos/{slug}/releases/latest", token)
    if isinstance(release, dict) and not release.get("draft"):
        version = release.get("tag_name") or release.get("name")
        updated_at = release.get("published_at") or release.get("created_at")

    if not version:
        tags = github_api_get(f"repos/{slug}/tags?per_page=1", token)
        if isinstance(tags, list) and tags:
            tag = tags[0]
            version = tag.get("name") or version
            commit_sha = (tag.get("commit") or {}).get("sha")
            if commit_sha:
                commit = github_api_get(f"repos/{slug}/commits/{commit_sha}", token)
                if isinstance(commit, dict):
                    updated_at = (
                        ((commit.get("commit") or {}).get("committer") or {}).get("date")
                        or updated_at
                    )

    if not version:
        branch_commit = github_api_get(f"repos/{slug}/commits/{default_branch}", token)
        if isinstance(branch_commit, dict):
            commit_sha = branch_commit.get("sha") or ""
            version = commit_sha[:7] if commit_sha else None
            updated_at = (
                ((branch_commit.get("commit") or {}).get("committer") or {}).get("date")
                or updated_at
            )

    if not updated_at:
        updated_at = repo.get("pushed_at") or repo.get("updated_at")

    return version, updated_at


def format_timestamp(raw_timestamp: str | None) -> str:
    if not raw_timestamp:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    try:
        parsed = datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
    except ValueError:
        return raw_timestamp

    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


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


def update_entries(
    entries: List[Dict[str, str]],
    repo_name: str,
    owner: str | None,
    provided_version: str | None,
    provided_updated_at: str | None,
    provided_url: str | None,
    token: str | None,
) -> List[Dict[str, str]]:
    display, slug, default_url = normalise_repo(repo_name, owner)

    provided_version = (provided_version or "").strip()
    provided_updated_at = (provided_updated_at or "").strip()

    fetched_version: str | None = None
    fetched_updated_at: str | None = None

    if not provided_version or not provided_updated_at:
        try:
            fetched_version, fetched_updated_at = fetch_repo_metadata(slug, token)
        except RuntimeError as exc:
            print(f"[update_readme] Warning: {exc}", file=sys.stderr)
            fetched_version = None
            fetched_updated_at = None

    version = provided_version or fetched_version or "unreleased"
    updated_source = provided_updated_at or fetched_updated_at
    updated_iso = format_timestamp(updated_source)

    url = (provided_url or "").strip() or default_url

    new_entry = {
        "display": display,
        "version": version,
        "updated": updated_iso,
        "url": url,
        "key": url,
    }

    filtered_entries = [
        entry for entry in entries if entry["key"].rstrip("/") != new_entry["key"].rstrip("/")
    ]
    return [new_entry] + filtered_entries


def load_repositories_from_env() -> List[Dict[str, str]] | None:
    raw_entries = os.getenv("REPO_ENTRIES", "").strip()
    if not raw_entries:
        return None

    try:
        parsed = json.loads(raw_entries)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise ValueError("REPO_ENTRIES must be valid JSON") from exc

    if not isinstance(parsed, list):
        raise ValueError("REPO_ENTRIES must be a JSON array of repository definitions")

    entries: List[Dict[str, str]] = []
    for index, item in enumerate(parsed):
        if not isinstance(item, dict):
            raise ValueError(
                f"REPO_ENTRIES item at index {index} must be an object with at least a 'name' field"
            )
        if not item.get("name"):
            raise ValueError(
                f"REPO_ENTRIES item at index {index} is missing the required 'name' field"
            )
        entries.append(
            {
                "name": str(item["name"]),
                "owner": item.get("owner"),
                "version": item.get("version"),
                "updated_at": item.get("updated_at"),
                "url": item.get("url"),
            }
        )

    return entries


def main() -> None:
    readme_path = Path(__file__).resolve().parents[1] / "README.md"
    if not readme_path.exists():
        raise FileNotFoundError("README.md not found")

    token = os.getenv("GITHUB_TOKEN", "").strip() or None

    repo_entries = load_repositories_from_env()

    if repo_entries is None:
        repo_name = os.getenv("REPO_NAME")
        if not repo_name:
            raise ValueError("REPO_NAME environment variable is required when REPO_ENTRIES is not provided")

        repo_entries = [
            {
                "name": repo_name,
                "owner": os.getenv("GITHUB_REPOSITORY_OWNER"),
                "version": os.getenv("REPO_VERSION"),
                "updated_at": os.getenv("REPO_UPDATED_AT"),
                "url": os.getenv("REPO_URL"),
            }
        ]

    with readme_path.open("r", encoding="utf-8") as fh:
        readme_lines = fh.readlines()

    readme_lines = ensure_markers(readme_lines)

    start_index = find_marker_index(readme_lines, MARKER_START)
    end_index = find_marker_index(readme_lines, MARKER_END)

    table_lines = readme_lines[start_index + 1 : end_index]
    entries = parse_table(table_lines)

    # Process repositories in reverse so the first item in the payload stays
    # at the top of the rendered table.
    for repo in reversed(repo_entries):
        entries = update_entries(
            entries,
            repo_name=repo["name"],
            owner=repo.get("owner") or os.getenv("GITHUB_REPOSITORY_OWNER"),
            provided_version=repo.get("version"),
            provided_updated_at=repo.get("updated_at"),
            provided_url=repo.get("url"),
            token=token,
        )

    new_table = build_table(entries)

    updated_section = [line + "\n" for line in new_table]
    new_readme_lines = readme_lines[: start_index + 1] + updated_section + readme_lines[end_index:]

    if new_readme_lines != readme_lines:
        with readme_path.open("w", encoding="utf-8") as fh:
            fh.writelines(new_readme_lines)


if __name__ == "__main__":
    main()