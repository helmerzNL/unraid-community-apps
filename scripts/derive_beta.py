#!/usr/bin/env python3
"""Make the beta templates from the stable templates, so the two cannot drift apart.

Every key of `sources.json` has a stable template `templates/<key>.xml` and a generated `templates/<key>-beta.xml`. The beta template is the
stable one with: the name "<name> (beta)", the `beta` tag of the image, `<Beta>true</Beta>` (so the store labels it), its own TemplateURL, its
own app data folder (`<key>-beta`), the host port plus one, a hidden variable that makes the app show a BETA label, and a notice in the
Overview. Edit the stable template, then run this script.

Usage:
    python scripts/derive_beta.py            write the beta templates
    python scripts/derive_beta.py --check    report beta templates that differ from what would be written (exit code 1)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NOTICE = (
    "Beta (English): the development version of the app, built from its beta branch. It can be unstable and can change the database in ways "
    "the stable version cannot read. It has its own app data folder and port, so it runs next to the stable app. Do not point it at the data "
    "of the stable app.[br][br]"
    "Bèta (Nederlands): de ontwikkelversie van de app, gebouwd vanaf de beta-branch. Hij kan onstabiel zijn en de database aanpassen op een "
    "manier die de stabiele versie niet kan lezen. Hij heeft een eigen data-map en poort, dus hij draait naast de stabiele app. Gebruik de "
    "data van de stabiele app niet voor de beta.[br][br]"
)


def sources(root: Path = ROOT) -> dict:
    return json.loads((root / "sources.json").read_text(encoding="utf-8"))


def _once(pattern: str, replacement: str, text: str) -> str:
    result, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if not count:
        raise SystemExit(f"the stable template has nothing that matches {pattern!r}")
    return result


def beta_template(stable: str, key: str, source: dict) -> str:
    text = _once(r"<Name>(.*?)</Name>", r"<Name>\1 (beta)</Name>", stable)
    text = _once(r"(<Repository>[^<]*):latest</Repository>", r"\1:beta</Repository>", text)
    text = _once(r"(  <Privileged>[^<]*</Privileged>\n)", r"\1  <Beta>true</Beta>\n", text)
    text = _once(rf"/templates/{re.escape(key)}\.xml</TemplateURL>", f"/templates/{key}-beta.xml</TemplateURL>", text)
    text = _once(r"<Overview>", "<Overview>" + NOTICE.replace("\\", "\\\\"), text)
    text = text.replace(f"/mnt/user/appdata/{key}", f"/mnt/user/appdata/{key}-beta")

    def next_port(match: re.Match) -> str:
        """The host port plus one, in the Default attribute and in the value of the entry."""
        line = match.group(0)
        line = re.sub(r'Default="(\d+)"', lambda m: f'Default="{int(m.group(1)) + 1}"', line, count=1)
        return re.sub(r">(\d+)</Config>$", lambda m: f">{int(m.group(1)) + 1}</Config>", line)
    text = re.sub(r'^  <Config [^\n]*Type="Port"[^\n]*</Config>$', next_port, text, flags=re.M)
    label = (f'  <Config Name="Beta label" Target="{source["beta_env"]}" Default="beta" Mode="" Description="Makes the app show a BETA label. '
             f'Do not change." Type="Variable" Display="always-hide" Required="false" Mask="false">beta</Config>\n')
    return _once(r"</Container>", label + "</Container>", text)


def expected(root: Path = ROOT) -> dict[Path, str]:
    return {root / "templates" / f"{key}-beta.xml": beta_template((root / "templates" / f"{key}.xml").read_text(encoding="utf-8"), key, source)
            for key, source in sources(root).items()}


def differences(root: Path = ROOT) -> list[str]:
    problems = []
    wanted = expected(root)
    for path, text in wanted.items():
        if not path.is_file():
            problems.append(f"missing: {path.relative_to(root)}")
        elif path.read_text(encoding="utf-8") != text:
            problems.append(f"differs: {path.relative_to(root)}")
    for path in sorted((root / "templates").glob("*-beta.xml")):
        if path not in wanted:
            problems.append(f"does not belong: {path.relative_to(root)}")
    return problems


if __name__ == "__main__":
    if "--check" in sys.argv[1:]:
        found = differences()
        print("\n".join(found) or "The beta templates match their stable templates.")
        sys.exit(1 if found else 0)
    for path, text in expected().items():
        path.write_text(text, encoding="utf-8")
    print("Written:", ", ".join(path.name for path in expected()))
