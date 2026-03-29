#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path
from typing import Optional


def load_ignore(path: Optional[Path]) -> set[str]:
    if not path or not path.exists():
        return set()
    ignored = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        ignored.add(line)
    return ignored


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail build when pip-audit finds vulnerabilities.")
    parser.add_argument("--report", default=".reports/pip-audit.json")
    parser.add_argument("--ignore-file", default="security/pip_audit_ignore.txt")
    args = parser.parse_args()

    report_path = Path(args.report)
    if not report_path.exists():
        print(f"pip-audit report not found: {report_path}")
        return 2

    data = json.loads(report_path.read_text(encoding="utf-8"))
    ignored = load_ignore(Path(args.ignore_file))

    findings = []
    for dep in data.get("dependencies", []):
        pkg = dep.get("name")
        version = dep.get("version")
        for vuln in dep.get("vulns", []):
            vuln_id = vuln.get("id")
            aliases = set(vuln.get("aliases", []))
            if vuln_id in ignored or aliases.intersection(ignored):
                continue
            findings.append(
                {
                    "package": pkg,
                    "version": version,
                    "id": vuln_id,
                    "aliases": sorted(aliases),
                    "fix_versions": vuln.get("fix_versions", []),
                }
            )

    if findings:
        print(f"pip-audit gate failed: {len(findings)} unignored vulnerabilities")
        for item in findings:
            aliases = ",".join(item["aliases"]) if item["aliases"] else "-"
            fixes = ",".join(item["fix_versions"]) if item["fix_versions"] else "none"
            print(
                f"- {item['package']}=={item['version']}: {item['id']} aliases=[{aliases}] fixed=[{fixes}]"
            )
        return 1

    print("pip-audit gate passed: no unignored vulnerabilities found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
