#!/usr/bin/env python3
"""Check that every template of this repository is complete and consistent with what the Community Applications store expects.

Run `python scripts/check_templates.py`; exit code 1 and one line per problem when something is wrong. Add `--registry` to also ask ghcr.io
whether the `latest` and `beta` tags of every image exist (needs the network). The store's own Validate and Scan, at
https://ca.unraid.net/submit, remains the authoritative check.
"""
from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIRED = ("Name", "Repository", "Registry", "Network", "Support", "Project", "Overview", "Category", "WebUI", "TemplateURL", "Icon", "Date")
SECRET_WORDS = ("SECRET", "TOKEN", "API_KEY", "PASSWORD", "ENCRYPTION_KEY")
CATEGORY = re.compile(r"^(?:[A-Za-z]+:(?:[A-Za-z]+)? ?)+$")
RAW = "https://raw.githubusercontent.com/helmerzNL/unraid-community-apps/main"


def load(path: Path) -> ET.Element:
    return ET.parse(path).getroot()


def text(root: ET.Element, tag: str) -> str:
    node = root.find(tag)
    return (node.text or "").strip() if node is not None else ""


def check_template(path: Path, root_dir: Path, sources: dict) -> list[str]:
    name, problems = path.name, []
    try:
        root = load(path)
    except ET.ParseError as error:
        return [f"{name}: not well-formed XML ({error})"]
    if root.tag != "Container" or root.get("version") != "2":
        problems.append(f'{name}: the root must be <Container version="2">')
    problems += [f"{name}: <{tag}> is missing or empty" for tag in REQUIRED if not text(root, tag)]
    key = path.stem.removesuffix("-beta")
    beta = path.stem.endswith("-beta")
    if key not in sources:
        problems.append(f"{name}: not listed in sources.json")
        return problems
    repository = text(root, "Repository")
    wanted = f"{sources[key]['image']}:{'beta' if beta else 'latest'}"
    if repository != wanted:
        problems.append(f"{name}: Repository is {repository!r}, expected {wanted!r} (lowercase, with its tag)")
    if (text(root, "Beta") == "true") != beta:
        problems.append(f"{name}: <Beta>true</Beta> belongs on the beta template and only there")
    if text(root, "TemplateURL") != f"{RAW}/templates/{path.name}":
        problems.append(f"{name}: TemplateURL must be {RAW}/templates/{path.name}")
    icon = text(root, "Icon")
    if not icon.startswith(f"{RAW}/icons/") or not (root_dir / "icons" / icon.rsplit("/", 1)[-1]).is_file():
        problems.append(f"{name}: Icon must be the raw URL of a file in icons/")
    category = text(root, "Category")
    if category and not CATEGORY.match(category):
        problems.append(f"{name}: Category {category!r} should look like 'Productivity:' or 'Tools: Other:'")
    overview = text(root, "Overview")
    if "English:" not in overview or "Nederlands:" not in overview:
        problems.append(f"{name}: the Overview needs an 'English:' and a 'Nederlands:' paragraph")
    if re.search(r"<|>", overview) or re.sub(r"\[/?br\]", "", overview).count("[") > 0:
        problems.append(f"{name}: the Overview must be plain text (only [br] is allowed); the store treats <something> as HTML")
    for config in root.findall("Config"):
        label = config.get("Name", "?")
        if config.get("Type") not in ("Port", "Path", "Variable"):
            problems.append(f"{name}: Config {label!r} has Type {config.get('Type')!r}")
        if not config.get("Description"):
            problems.append(f"{name}: Config {label!r} has no Description")
        if re.search(r"<|>", config.get("Description", "")):
            problems.append(f"{name}: Config {label!r}: no <...> in a Description (the store treats it as HTML)")
        target = config.get("Target", "")
        is_switch = "|" in config.get("Default", "")          # a dropdown (true|false) is a setting, not a secret, whatever its name says
        if config.get("Type") == "Variable" and not is_switch and any(word in target.upper() for word in SECRET_WORDS) and config.get("Mask") != "true":
            problems.append(f"{name}: Config {label!r} ({target}) holds a secret and must have Mask=\"true\"")
        default = config.get("Default", "")
        value = (config.text or "").strip()
        if "|" in default:
            if value not in default.split("|"):
                problems.append(f"{name}: Config {label!r}: the value {value!r} is not one of the choices {default!r}")
        elif value != default:
            problems.append(f"{name}: Config {label!r}: the value {value!r} differs from Default {default!r}")
        if config.get("Required") == "true" and config.get("Display") not in ("always", "always-hide"):
            problems.append(f"{name}: Config {label!r} is required and must be Display=\"always\"")
    configs = {c.get("Target"): c for c in root.findall("Config")}
    host_data = configs.get("DISCVAULT_DATA_DIR_HOST")
    if host_data is not None and (configs.get("/data") is None or configs["/data"].get("Default") != host_data.get("Default")):
        problems.append(f"{name}: DISCVAULT_DATA_DIR_HOST must be the same folder as the /data path")
    for config in root.findall("Config"):
        if config.get("Type") == "Path" and config.get("Target") != "/var/run/docker.sock" and not config.get("Default", "").startswith("/mnt/user/appdata/"):
            problems.append(f"{name}: Config {config.get('Name')!r}: a Path default belongs under /mnt/user/appdata/")
    webui = text(root, "WebUI")
    ports = [c.get("Target") for c in root.findall("Config") if c.get("Type") == "Port"]
    match = re.search(r"\[PORT:(\d+)\]", webui)
    if not match or match.group(1) not in ports:
        problems.append(f"{name}: WebUI must use [PORT:<container port>] of a Port entry ({ports})")
    return problems


def check_repository(root_dir: Path = ROOT, registry: bool = False) -> list[str]:
    problems = []
    for required in ("LICENSE", "README.md", "ca_profile.xml", "sources.json"):
        if not (root_dir / required).is_file():
            problems.append(f"{required} is missing in the root of the repository")
    if (root_dir / "ca_profile.xml").is_file():
        try:
            profile = load(root_dir / "ca_profile.xml")
            if not text(profile, "Profile"):
                problems.append("ca_profile.xml: <Profile> is empty, and the store blocks a submission without it")
        except ET.ParseError as error:
            problems.append(f"ca_profile.xml: not well-formed XML ({error})")
    sources = json.loads((root_dir / "sources.json").read_text(encoding="utf-8"))
    files = sorted((root_dir / "templates").glob("*.xml"))
    for key in sources:
        for suffix in ("", "-beta"):
            if not (root_dir / "templates" / f"{key}{suffix}.xml").is_file():
                problems.append(f"templates/{key}{suffix}.xml is missing")
    names, host_ports, paths, stacks = {}, {}, {}, {}
    for path in files:
        problems += check_template(path, root_dir, sources)
        try:
            root = load(path)
        except ET.ParseError:
            continue
        name = text(root, "Name")
        if name in names:
            problems.append(f"{path.name}: the Name {name!r} is also used by {names[name]}; names must be unique")
        names[name] = path.name
        for config in root.findall("Config"):
            if config.get("Type") == "Port":
                other = host_ports.setdefault(config.get("Default"), path.name)
                if other != path.name:
                    problems.append(f"{path.name}: host port {config.get('Default')} is also used by {other}")
            folder = config.get("Default", "")
            is_folder = (config.get("Type") == "Path" and folder.startswith("/mnt/user/appdata/")) or config.get("Target", "").endswith("_DIR_HOST")
            if is_folder:
                other = paths.setdefault(folder, path.name)
                if other != path.name:
                    problems.append(f"{path.name}: folder {folder} is also used by {other}")
            if config.get("Target") in ("DISCVAULT_PROJECT_NAME", "DISCVAULT_NETWORK"):
                other = stacks.setdefault((config.get("Target"), (config.text or "").strip()), path.name)
                if other != path.name:
                    problems.append(f"{path.name}: {config.get('Target')} {(config.text or '').strip()!r} is also used by {other}")
    if registry:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        problems += check_registry(sources)
    return problems


def check_registry(sources: dict) -> list[str]:
    """Whether the latest and beta tags exist on ghcr.io (anonymous, public images)."""
    import urllib.error
    import urllib.parse
    import urllib.request

    def get(url, headers=None, method="GET"):
        return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "unraid-community-apps-check", **(headers or {})}, method=method), timeout=30)

    problems = []
    accept = "application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.manifest.v1+json"
    for key, source in sources.items():
        host, _, image = source["image"].partition("/")
        try:
            with get(f"https://{host}/token?service={host}&scope={urllib.parse.quote(f'repository:{image}:pull')}") as response:
                token = json.load(response)["token"]
            for tag in ("latest", "beta"):
                try:
                    with get(f"https://{host}/v2/{image}/manifests/{tag}", {"Authorization": f"Bearer {token}", "Accept": accept}, "HEAD"):
                        pass
                except urllib.error.HTTPError as error:
                    problems.append(f"{key}: the image {source['image']}:{tag} cannot be pulled anonymously ({error.code})")
        except OSError as error:
            problems.append(f"{key}: the registry could not be reached ({error})")
    return problems


if __name__ == "__main__":
    found = check_repository(registry="--registry" in sys.argv[1:])
    print("\n".join(found) or f"All {len(list((ROOT / 'templates').glob('*.xml')))} templates are in order.")
    sys.exit(1 if found else 0)
