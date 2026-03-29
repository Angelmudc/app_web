#!/usr/bin/env python3
import argparse
import re
import sys
from pathlib import Path

from packaging.utils import canonicalize_name

REQ_RE = re.compile(r"^([A-Za-z0-9_.-]+)(?:\[[^\]]+\])?==([^\s;]+)(?:\s*;.*)?$")


def load_names(path: Path) -> set[str]:
    names: set[str] = set()
    if not path.exists():
        return names
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        names.add(canonicalize_name(line))
    return names


def parse_requirements(path: Path):
    entries = []
    violations = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("-r", "--requirement", "-e", "--editable", "-c", "--constraint")):
            violations.append((line_no, "Unsupported requirement include/editable/constraint", raw))
            continue
        m = REQ_RE.match(line)
        if not m:
            violations.append((line_no, "Requirement must use exact pin: package==version", raw))
            continue
        name = canonicalize_name(m.group(1))
        version = m.group(2)
        entries.append((line_no, name, version, raw))
    return entries, violations


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate dependency policy for requirements files.")
    parser.add_argument("--requirements", default="requirements.txt")
    parser.add_argument("--allowlist", default="security/dependency_allowlist.txt")
    parser.add_argument("--denylist", default="security/dependency_denylist.txt")
    args = parser.parse_args()

    req_path = Path(args.requirements)
    allowlist = load_names(Path(args.allowlist))
    denylist = load_names(Path(args.denylist))

    entries, violations = parse_requirements(req_path)

    for line_no, name, version, raw in entries:
        if allowlist and name not in allowlist:
            violations.append((line_no, "Package not in approved allowlist (possible typosquatting/new dependency)", raw))
        if name in denylist:
            violations.append((line_no, "Package is explicitly blocked by denylist", raw))
        if version in {"*", "latest"}:
            violations.append((line_no, "Floating versions are not allowed", raw))

    if violations:
        print("Dependency policy violations found:")
        for line_no, reason, raw in violations:
            print(f"- line {line_no}: {reason}: {raw}")
        return 1

    print(f"Dependency policy OK: {len(entries)} pinned dependencies validated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
