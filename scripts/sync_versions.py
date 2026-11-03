#!/usr/bin/env python3
"""Record the newest stable and beta version of every app in its templates, once the image of that version is published.

The templates use the floating tags `latest` and `beta`, so Unraid offers an update by itself whenever such a tag moves; nothing here is
needed for that. What this script keeps current is the `<Changes>` field of each template, the change log the Community Applications
store shows next to an app: its newest entry says which version the stable or beta template delivers, with a link to what changed.

`sources.json` says per app, under `versions`, which release tags give the version and which image proves that it is published:

    "stable": {"tag": "^v(\\d+\\.\\d+\\.\\d+)$", "image": "ghcr.io/helmerznl/doorbell-map:v{version}"},
    "beta":   {"tag": "^v(\\d+\\.\\d+\\.\\d+)$", "image": "ghcr.io/helmerznl/doorbell-map:beta", "branch": "beta"}

An image with `{version}` must exist for that version before it is recorded (a release tag is often set before its image is built). An
image without it is a moving tag, which has no version of its own: the version is then `<newest release>-beta.<build time of that image>`,
for example `1.0.34-beta.202611031626`, the same scheme the Home Assistant add-ons of helmerzNL/home-assistant-addons use, so both
repositories name a beta build the same way.

The stable template is written here and by hand; the beta template is generated from it by `scripts/derive_beta.py`, which keeps the
`<Changes>` of the beta template as this script wrote them.

Usage:
    python scripts/sync_versions.py            write the new versions into the templates
    python scripts/sync_versions.py --check    only report what would change (exit code 0)

GITHUB_TOKEN (optional) raises the rate limit of the GitHub API. The GitHub and registry helpers below are the ones of
scripts/sync_versions.py in helmerzNL/home-assistant-addons; keep the two in step.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape, unescape

sys.path.insert(0, str(Path(__file__).resolve().parent))
from derive_beta import CHANGES, with_changes  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
KEEP = 10                       # entries per <Changes>; older ones are dropped
MANIFEST_TYPES = ", ".join((
    "application/vnd.oci.image.index.v1+json", "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json", "application/vnd.docker.distribution.manifest.v2+json"))


def version_key(version: str) -> tuple:
    """Sort key for X.Y.Z, X.Y.Z-beta and X.Y.Z-beta.<stamp> (numbers compared as numbers)."""
    return tuple(int(part) for part in re.findall(r"\d+", version))


def newest(tags: list[str], pattern: str) -> str | None:
    """The highest version among the tags that match the pattern (its first group is the version), or None."""
    versions = [m.group(1) for tag in tags if (m := re.fullmatch(pattern, tag))]
    return max(versions, key=version_key) if versions else None


def beta_version(base: str, created: str) -> str:
    """`1.0.34-beta.202611031626`: the newest release plus the build time (UTC, to the minute) of the beta image."""
    return f"{base}-beta.{re.sub(r'[^0-9]', '', created)[:12]}"


def _get(url: str, headers: dict | None = None, method: str = "GET"):
    request = urllib.request.Request(url, headers={"User-Agent": "unraid-community-apps-sync", **(headers or {})}, method=method)
    return urllib.request.urlopen(request, timeout=30)


def repo_tags(repo: str) -> list[str]:
    headers = {"Accept": "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    tags, page = [], 1
    while True:
        with _get(f"https://api.github.com/repos/{repo}/tags?per_page=100&page={page}", headers) as response:
            batch = json.load(response)
        tags += [t["name"] for t in batch]
        if len(batch) < 100 or page >= 5:
            return tags
        page += 1


def _registry(reference: str) -> tuple[str, str, str, dict]:
    """Host, name and tag of an image reference, and the headers to read it anonymously (the images are public)."""
    image, _, tag = reference.rpartition(":")
    host, _, name = image.partition("/")
    with _get(f"https://{host}/token?service={host}&scope={urllib.parse.quote(f'repository:{name}:pull')}") as response:
        token = json.load(response)["token"]
    return host, name, tag, {"Authorization": f"Bearer {token}", "Accept": MANIFEST_TYPES}


def image_exists(reference: str) -> bool:
    """Whether the image `<image>:<tag>` is published."""
    host, name, tag, headers = _registry(reference)
    try:
        with _get(f"https://{host}/v2/{name}/manifests/{tag}", headers, "HEAD"):
            return True
    except urllib.error.HTTPError as error:
        if error.code in (401, 403, 404):
            return False
        raise


def image_build(reference: str) -> dict | None:
    """When the image `<image>:<tag>` was built and from which commit (its amd64 config), or None when the tag does not exist."""
    host, name, tag, headers = _registry(reference)
    try:
        with _get(f"https://{host}/v2/{name}/manifests/{tag}", headers) as response:
            manifest = json.load(response)
        if "manifests" in manifest:
            wanted = [m for m in manifest["manifests"] if m.get("platform", {}).get("architecture") == "amd64"]
            with _get(f"https://{host}/v2/{name}/manifests/{wanted[0]['digest']}", headers) as response:
                manifest = json.load(response)
        with _get(f"https://{host}/v2/{name}/blobs/{manifest['config']['digest']}", headers) as response:
            config = json.load(response)
    except urllib.error.HTTPError as error:
        if error.code in (401, 403, 404):
            return None
        raise
    labels = (config.get("config") or {}).get("Labels") or {}
    return {"created": config.get("created"), "revision": labels.get("org.opencontainers.image.revision", "")}


def entry(version: str, repo: str, channel: dict, build: dict | None = None, base: str | None = None) -> str:
    """One entry of <Changes> (plain text, the newest version as its heading)."""
    if build is None:
        return (f"### {version}\n- Version {version} of the app. / Versie {version} van de app.\n"
                f"- Release notes / release-teksten: https://github.com/{repo}/releases/tag/v{version}\n")
    stamp = re.sub(r"[^0-9]", "", build["created"])[:12]
    when = f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]} {stamp[8:10]}:{stamp[10:12]} UTC"
    if re.fullmatch(r"[0-9a-f]{40}", build.get("revision") or ""):
        link = f"https://github.com/{repo}/compare/v{base}...{build['revision']}"
    else:
        link = f"https://github.com/{repo}/commits/{channel.get('branch', 'beta')}"
    return (f"### {version}\n- Beta build of {when}, after release {base}. / Beta-build van {when}, na release {base}.\n"
            f"- What changed since that release / wat er sinds die release veranderde: {link}\n")


def entries(template: str) -> list[str]:
    """The entries of the <Changes> of a template, newest first."""
    match = CHANGES.search(template)
    if not match:
        return []
    text = unescape(re.sub(r"^  <Changes>|</Changes>\n$", "", match.group(0)))
    return [f"### {part.strip()}\n" for part in re.split(r"^### ", text, flags=re.M) if part.strip()]


def recorded(template: str) -> str | None:
    """The version of the newest entry of <Changes>, or None when the template has none yet."""
    found = entries(template)
    return found[0].split("\n", 1)[0].removeprefix("### ").strip() if found else None


def with_entry(template: str, new: str) -> str:
    """The template with `new` on top of its <Changes> (made when missing), keeping the newest KEEP entries."""
    kept = [new] + entries(template)[:KEEP - 1]
    block = "  <Changes>" + escape("\n".join(kept).rstrip("\n")) + "</Changes>\n"
    return with_changes(CHANGES.sub("", template), block)


def target(source: dict, channel: dict, tags_of, exists, build_of) -> tuple[str | None, dict | None, str | None, str | None]:
    """(version, build, base, problem) for one channel of an app: what the template should say, or why it stays."""
    latest = newest(tags_of(source["app_repo"]), channel["tag"])
    if latest is None:
        return None, None, None, f"no release tag matches {channel['tag']}"
    if "{version}" in channel["image"]:
        reference = channel["image"].format(version=latest)
        if not exists(reference):
            return None, None, None, f"{latest} is released, but the image {reference} is not published yet"
        return latest, None, None, None
    build = build_of(channel["image"])
    if not build or not build.get("created"):
        return None, None, None, f"the image {channel['image']} is not published"
    return beta_version(latest, build["created"]), build, latest, None


def sync(root: Path = ROOT, write: bool = True, tags_of=repo_tags, exists=image_exists, build_of=image_build) -> list[str]:
    """Record the versions; returns one line per template that changed (or would change), or that cannot follow yet."""
    sources = json.loads((root / "sources.json").read_text(encoding="utf-8"))
    report = []
    for key, source in sorted(sources.items()):
        for channel_name, suffix in (("stable", ""), ("beta", "-beta")):
            name = f"{key}{suffix}"
            path = root / "templates" / f"{name}.xml"
            text = path.read_text(encoding="utf-8")
            have = recorded(text)
            channel = source["versions"][channel_name]
            version, build, base, problem = target(source, channel, tags_of, exists, build_of)
            if problem:
                report.append(f"{name}: {problem}; staying on {have or 'nothing recorded'}")
                continue
            if have and version_key(version) <= version_key(have):
                continue
            report.append(f"{name}: {have or 'nothing recorded'} -> {version}")
            if write:
                path.write_text(with_entry(text, entry(version, source["app_repo"], channel, build, base)), encoding="utf-8")
    return report


if __name__ == "__main__":
    lines = sync(write="--check" not in sys.argv[1:])
    print("\n".join(lines) or "Every template records the newest published version.")
