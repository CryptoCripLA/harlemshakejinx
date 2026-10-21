"""Phase 4 V19: verify the license of every dependency JINX runs or ships.

    .venv/bin/python scripts/phase4_license_audit.py --out docs/evidence/PHASE4_LICENSE_AUDIT_20261021.json

1. Python: the full transitive closure of requirements.txt as installed in the venv running this script, with each
   distribution's declared license (License-Expression, License, or license classifiers).
2. Node: the GMGN bridge's production dependencies from build/gmgn/package-lock.json (dev-only packages excluded),
   with the license each installed package declares.
3. Upstreams: each pinned upstream's license file matches the sha256 recorded in UPSTREAM_PINS.json, and each
   translated upstream ships its license under third_party/.
4. docs/LICENSES.md lists every direct Python requirement.
A dependency whose license is unknown or strong copyleft (GPL, AGPL, SSPL, EUPL) makes the result FAIL and needs an
owner decision; a weak-copyleft library (LGPL) used unmodified as a separately installed package is reported as a
note. A license read from the project's own repository (not its package metadata) is listed with its source.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STRONG_COPYLEFT = re.compile(r"\b(?<!L)(A?GPL|SSPL|EUPL|GNU (Affero )?General Public)", re.I)
WEAK_COPYLEFT = re.compile(r"\b(LGPL|GNU Lesser General Public)", re.I)
# Licenses not declared in the installed package metadata, read from the project's own repository.
FROM_SOURCE = {"jsonalias": ("MIT", "https://github.com/kevinheavey/jsonalias/blob/master/LICENSE, read 2026-10-21 "
                                    "(package metadata declares none)")}


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def requirement_names(lines) -> list[str]:
    """Direct requirements, plus the packages their requested extras pull in (psycopg[binary] -> psycopg-binary)."""
    out = []
    for line in lines:
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        name = norm(re.split(r"[\[<>=!~; ]", line, maxsplit=1)[0])
        out.append(name)
        extras = re.search(r"\[([^\]]+)\]", line)
        if extras:
            try:
                dist = metadata.distribution(name)
            except metadata.PackageNotFoundError:
                continue
            for req in dist.requires or []:
                if any(f'extra == "{e.strip()}"' in req for e in extras.group(1).split(",")):
                    out.append(norm(re.split(r"[\[<>=!~; (]", req, maxsplit=1)[0]))
    return out


def dist_license(d: metadata.Distribution) -> str:
    m = d.metadata
    expr = m.get("License-Expression")
    if expr:
        return expr
    classifiers = [c.split("::")[-1].strip() for c in m.get_all("Classifier") or [] if c.startswith("License ::")]
    lic = (m.get("License") or "").strip()
    if lic and len(lic) < 80 and lic.upper() != "UNKNOWN":
        return lic + (f" ({'; '.join(classifiers)})" if classifiers else "")
    if classifiers:
        return "; ".join(classifiers)
    if lic:
        return lic.splitlines()[0][:80]
    for f in d.files or []:                      # a shipped license file, e.g. solders: Apache License 2.0
        if "LICEN" in str(f).upper():
            head = " ".join(d.locate_file(f).read_text(errors="replace").split()[:4])
            return f"{head} (license file {f.name})"
    return "UNKNOWN"


def marker_ok(req: str) -> bool:
    try:
        from packaging.requirements import Requirement
    except ImportError:                          # pragma: no cover
        from pip._vendor.packaging.requirements import Requirement
    r = Requirement(req)
    return r.marker is None or r.marker.evaluate({"extra": ""})


def python_closure(direct: list[str]) -> dict[str, dict]:
    dists = {norm(d.metadata["Name"]): d for d in metadata.distributions()}
    seen, todo = {}, list(direct)
    while todo:
        name = todo.pop()
        if name in seen or name not in dists:
            if name not in dists:
                seen[name] = {"version": None, "license": "NOT INSTALLED"}
            continue
        d = dists[name]
        lic = dist_license(d)
        entry = {"version": d.version, "license": lic, "direct": name in direct}
        if lic == "UNKNOWN" and name in FROM_SOURCE:
            entry["license"], entry["license_source"] = FROM_SOURCE[name]
        seen[name] = entry
        for req in d.requires or []:
            if "extra ==" in req or not marker_ok(req):
                continue
            todo.append(norm(re.split(r"[\[<>=!~; (]", req, maxsplit=1)[0]))
    return seen


def node_production(build: Path) -> dict[str, dict]:
    lock = json.loads((build / "package-lock.json").read_text())
    out = {}
    for path, info in (lock.get("packages") or {}).items():
        if not path.startswith("node_modules/") or info.get("dev"):
            continue
        name = path.split("node_modules/")[-1]
        pkg = build / path / "package.json"
        lic = info.get("license")
        if pkg.exists():
            lic = json.loads(pkg.read_text()).get("license", lic)
        out[name] = {"version": info.get("version"), "license": lic or "UNKNOWN"}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    args = ap.parse_args()
    problems, notes = [], []
    direct = requirement_names((ROOT / "requirements.txt").read_text().splitlines())
    py = python_closure(direct)
    node = node_production(ROOT / "build" / "gmgn") if (ROOT / "build" / "gmgn" / "package-lock.json").exists() else {}
    for kind, deps in (("python", py), ("node", node)):
        for name, d in deps.items():
            if d["license"] in ("UNKNOWN", "NOT INSTALLED") or STRONG_COPYLEFT.search(str(d["license"])):
                problems.append(f"{kind} {name} {d.get('version')}: {d['license']}")
            elif WEAK_COPYLEFT.search(str(d["license"])):
                notes.append(f"{kind} {name} {d.get('version')}: {d['license']}: weak copyleft; JINX installs it "
                             f"unmodified from PyPI and links it at run time (no copy, no modification, not vendored)")
    pins = json.loads((ROOT / "UPSTREAM_PINS.json").read_text())["repos"]
    upstreams = {}
    for name, pin in pins.items():
        f = pin.get("license_file")
        if f is None:
            upstreams[name] = {"license": pin["license"], "reuse": pin["reuse"], "verified": pin["reuse"] == "reference-only"}
            continue
        p = ROOT / "upstream" / name / f
        ok = p.exists() and hashlib.sha256(p.read_bytes()).hexdigest() == pin["license_sha256"]
        upstreams[name] = {"license": pin["license"], "reuse": pin["reuse"], "license_sha256_matches_pin": ok}
        if not ok:
            problems.append(f"upstream {name}: license file missing or changed")
    shipped = {p.parent.name: p.read_text()[:60].strip().splitlines()[0] for p in (ROOT / "third_party").glob("*/LICENSE*")}
    for name in ("d3ad", "gmgn"):
        if name not in shipped:
            problems.append(f"third_party/{name}/LICENSE missing")
    rows = {norm(l.split("|")[1].strip().split(" ")[0]) for l in (ROOT / "docs" / "LICENSES.md").read_text().splitlines()
            if l.startswith("| ") and not l.startswith("| ---")}
    unlisted = [n for n in dict.fromkeys(direct) if n not in rows]
    if unlisted:
        problems.append(f"docs/LICENSES.md does not list direct requirement(s): {', '.join(unlisted)}")
    report = {"utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "commit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                                       text=True).stdout.strip(),
              "python_interpreter": sys.executable, "python_direct": direct, "python": dict(sorted(py.items())),
              "node_production": dict(sorted(node.items())), "upstreams": upstreams,
              "third_party_licenses_shipped": shipped, "problems": problems, "notes": notes,
              "result": "PASS" if not problems else "FAIL"}
    text = json.dumps(report, indent=1)
    if args.out:
        Path(args.out).write_text(text + "\n")
    print(text)
    sys.exit(0 if not problems else 1)


if __name__ == "__main__":
    main()
