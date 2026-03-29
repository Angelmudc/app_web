#!/usr/bin/env python3
"""Operational backup/restore tooling for app_web.

Supports PostgreSQL and SQLite databases plus filesystem backup bundles.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urlsplit, urlunsplit

from utils.secrets_manager import get_secret

CRITICAL_TABLES_DEFAULT = [
    "staff_users",
    "staff_audit_logs",
    "candidatas",
    "clientes",
    "solicitudes",
]

DEFAULT_FILE_INCLUDES = [
    "migrations",
    "alembic.ini",
    "requirements.txt",
    "Procfile",
]

EXCLUDED_FILE_PATTERNS = {".env", "clave1.json", "service_account.json"}
SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class BackupToolError(RuntimeError):
    pass


def utc_now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def file_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def write_checksum(path: Path, digest: str) -> Path:
    checksum_path = path.with_suffix(path.suffix + ".sha256")
    checksum_path.write_text(f"{digest}  {path.name}\n", encoding="utf-8")
    return checksum_path


def write_metadata(path: Path, metadata: dict) -> Path:
    meta_path = path.with_suffix(path.suffix + ".metadata.json")
    meta_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return meta_path


def redact_url(url: str) -> str:
    try:
        parts = urlsplit(url)
        if not parts.netloc:
            return url
        host = parts.hostname or ""
        if parts.port:
            host = f"{host}:{parts.port}"
        user = parts.username or ""
        if user:
            netloc = f"{user}:***@{host}"
        else:
            netloc = host
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    except Exception:
        return "<redaction_failed>"


def detect_engine(database_url: str) -> str:
    lower = (database_url or "").strip().lower()
    if lower.startswith("postgres://") or lower.startswith("postgresql://") or lower.startswith("postgresql+"):
        return "postgres"
    if lower.startswith("sqlite://"):
        return "sqlite"
    raise BackupToolError("DATABASE_URL no soportada. Usa postgres/postgresql o sqlite.")


def sqlite_path_from_url(database_url: str) -> Path:
    url = (database_url or "").strip()
    if url.endswith(":memory:"):
        raise BackupToolError("SQLite en memoria no se puede respaldar/restaurar con archivo.")

    if not url.startswith("sqlite://"):
        raise BackupToolError("URL SQLite inválida.")

    # sqlite:////abs/path.db or sqlite:///relative.db
    raw = url[len("sqlite://") :]
    if "?" in raw:
        raw = raw.split("?", 1)[0]

    if raw.startswith("/"):
        return Path(raw)

    return Path.cwd() / raw


def run_command(cmd: list[str], env: dict | None = None, capture_output: bool = False) -> str:
    proc = subprocess.run(
        cmd,
        env=env,
        text=True,
        capture_output=capture_output,
        check=False,
    )
    if proc.returncode != 0:
        details = (proc.stderr or proc.stdout or "").strip()
        raise BackupToolError(f"Fallo ejecutando {' '.join(cmd)}: {details}")
    return proc.stdout if capture_output else ""


def backup_postgres(database_url: str, output_file: Path) -> None:
    cmd = [
        "pg_dump",
        "--format=custom",
        "--no-owner",
        "--no-privileges",
        f"--file={str(output_file)}",
        database_url,
    ]
    run_command(cmd)


def backup_sqlite(database_url: str, output_file: Path) -> None:
    source_path = sqlite_path_from_url(database_url)
    if not source_path.exists():
        raise BackupToolError(f"SQLite origen no existe: {source_path}")

    src = sqlite3.connect(str(source_path))
    dst = sqlite3.connect(str(output_file))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def verify_postgres_dump(dump_file: Path) -> None:
    run_command(["pg_restore", "--list", str(dump_file)])


def verify_sqlite_file(sqlite_file: Path) -> None:
    conn = sqlite3.connect(str(sqlite_file))
    try:
        row = conn.execute("PRAGMA integrity_check;").fetchone()
    finally:
        conn.close()
    result = str(row[0] if row else "").strip().lower()
    if result != "ok":
        raise BackupToolError(f"PRAGMA integrity_check devolvió: {result or '<empty>'}")


def infer_engine_from_backup(backup_file: Path, metadata: dict | None = None) -> str:
    if metadata and metadata.get("engine") in {"postgres", "sqlite", "files"}:
        return str(metadata["engine"])

    name = backup_file.name.lower()
    if name.endswith(".dump"):
        return "postgres"
    if name.endswith(".sqlite") or name.endswith(".db"):
        return "sqlite"
    if name.endswith(".tar.gz"):
        return "files"
    raise BackupToolError("No se pudo inferir tipo del backup. Usa metadata o extensión .dump/.sqlite/.tar.gz.")


def load_metadata_for_backup(backup_file: Path) -> dict | None:
    meta_path = backup_file.with_suffix(backup_file.suffix + ".metadata.json")
    if not meta_path.exists():
        return None
    return json.loads(meta_path.read_text(encoding="utf-8"))


def parse_checksum_file(checksum_file: Path) -> str:
    raw = checksum_file.read_text(encoding="utf-8").strip()
    if not raw:
        raise BackupToolError(f"Checksum vacío: {checksum_file}")
    return raw.split()[0].strip().lower()


def command_backup_db(args: argparse.Namespace) -> int:
    database_url = (args.database_url or get_secret("DATABASE_URL") or "").strip()
    if not database_url:
        raise BackupToolError("Falta --database-url y DATABASE_URL no está definida.")

    output_dir = Path(args.output_dir).resolve()
    ensure_dir(output_dir)

    engine = detect_engine(database_url)
    stamp = utc_now_stamp()
    ext = ".dump" if engine == "postgres" else ".sqlite"
    backup_file = output_dir / f"{args.prefix}_{stamp}{ext}"

    if engine == "postgres":
        backup_postgres(database_url, backup_file)
    else:
        backup_sqlite(database_url, backup_file)

    if args.verify:
        if engine == "postgres":
            verify_postgres_dump(backup_file)
        else:
            verify_sqlite_file(backup_file)

    digest = file_sha256(backup_file)
    checksum_file = write_checksum(backup_file, digest)
    metadata = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "engine": engine,
        "source_database_url_redacted": redact_url(database_url),
        "backup_file": backup_file.name,
        "backup_bytes": backup_file.stat().st_size,
        "sha256": digest,
        "integrity_verified": bool(args.verify),
    }
    metadata_file = write_metadata(backup_file, metadata)

    latest_file = output_dir / f"{args.prefix}.latest"
    latest_file.write_text(f"{backup_file.name}\n", encoding="utf-8")

    print(json.dumps({
        "status": "ok",
        "backup_file": str(backup_file),
        "checksum_file": str(checksum_file),
        "metadata_file": str(metadata_file),
        "engine": engine,
        "integrity_verified": bool(args.verify),
    }, ensure_ascii=False))
    return 0


def _iter_existing_paths(paths: Iterable[str]) -> list[Path]:
    result: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.exists():
            result.append(p)
    return result


def command_backup_files(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir).resolve()
    ensure_dir(output_dir)

    include_paths = list(args.include) if args.include else list(DEFAULT_FILE_INCLUDES)
    resolved = _iter_existing_paths(include_paths)

    if not resolved:
        raise BackupToolError("No hay rutas existentes para incluir en backup de archivos.")

    stamp = utc_now_stamp()
    bundle_file = output_dir / f"{args.prefix}_{stamp}.tar.gz"

    included_entries: list[str] = []
    skipped_sensitive: list[str] = []

    with tarfile.open(bundle_file, "w:gz") as tar:
        for path in resolved:
            if path.name in EXCLUDED_FILE_PATTERNS:
                skipped_sensitive.append(str(path))
                continue
            tar.add(path, arcname=str(path))
            included_entries.append(str(path))

    digest = file_sha256(bundle_file)
    checksum_file = write_checksum(bundle_file, digest)
    metadata = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "engine": "files",
        "bundle_file": bundle_file.name,
        "bundle_bytes": bundle_file.stat().st_size,
        "sha256": digest,
        "included_paths": included_entries,
        "skipped_sensitive_paths": skipped_sensitive,
        "note": "Secretos (.env/JSON credenciales) se respaldan por canal separado cifrado.",
    }
    metadata_file = write_metadata(bundle_file, metadata)

    print(json.dumps({
        "status": "ok",
        "bundle_file": str(bundle_file),
        "checksum_file": str(checksum_file),
        "metadata_file": str(metadata_file),
        "included_count": len(included_entries),
        "skipped_sensitive_count": len(skipped_sensitive),
    }, ensure_ascii=False))
    return 0


def command_verify_backup(args: argparse.Namespace) -> int:
    backup_file = Path(args.backup_file).resolve()
    if not backup_file.exists():
        raise BackupToolError(f"Backup no encontrado: {backup_file}")

    checksum_file = Path(args.checksum_file).resolve() if args.checksum_file else backup_file.with_suffix(backup_file.suffix + ".sha256")
    if not checksum_file.exists():
        raise BackupToolError(f"Checksum no encontrado: {checksum_file}")

    expected = parse_checksum_file(checksum_file)
    got = file_sha256(backup_file)
    if got.lower() != expected.lower():
        raise BackupToolError("Checksum inválido: el archivo pudo corromperse o alterarse.")

    metadata = load_metadata_for_backup(backup_file)
    engine = infer_engine_from_backup(backup_file, metadata)

    if not args.skip_integrity:
        if engine == "postgres":
            verify_postgres_dump(backup_file)
        elif engine == "sqlite":
            verify_sqlite_file(backup_file)
        else:
            with tarfile.open(backup_file, "r:gz") as tar:
                tar.getmembers()

    print(json.dumps({
        "status": "ok",
        "backup_file": str(backup_file),
        "checksum_ok": True,
        "engine": engine,
        "integrity_verified": not bool(args.skip_integrity),
    }, ensure_ascii=False))
    return 0


def _is_production_like() -> bool:
    env = (os.getenv("APP_ENV") or os.getenv("FLASK_ENV") or "").strip().lower()
    return env in {"prod", "production"}


def command_restore_db(args: argparse.Namespace) -> int:
    backup_file = Path(args.backup_file).resolve()
    if not backup_file.exists():
        raise BackupToolError(f"Backup no encontrado: {backup_file}")

    if _is_production_like() and not args.allow_prod:
        raise BackupToolError("Restore bloqueado en entorno production sin --allow-prod.")

    target_url = (args.target_database_url or get_secret("DATABASE_URL") or "").strip()
    if not target_url:
        raise BackupToolError("Falta --target-database-url y DATABASE_URL no está definida.")

    target_engine = detect_engine(target_url)
    metadata = load_metadata_for_backup(backup_file)
    backup_engine = infer_engine_from_backup(backup_file, metadata)
    if backup_engine == "files":
        raise BackupToolError("restore-db no aplica a backups de archivos (.tar.gz).")
    if target_engine != backup_engine:
        raise BackupToolError(f"Engine mismatch: backup={backup_engine} target={target_engine}")

    # Always verify checksum before attempting restore.
    checksum_path = backup_file.with_suffix(backup_file.suffix + ".sha256")
    if checksum_path.exists():
        expected = parse_checksum_file(checksum_path)
        got = file_sha256(backup_file)
        if expected.lower() != got.lower():
            raise BackupToolError("Checksum inválido; restore cancelado.")

    if args.dry_run:
        if backup_engine == "postgres":
            verify_postgres_dump(backup_file)
        else:
            verify_sqlite_file(backup_file)
        print(json.dumps({
            "status": "ok",
            "dry_run": True,
            "backup_file": str(backup_file),
            "target_database_url_redacted": redact_url(target_url),
            "engine": target_engine,
        }, ensure_ascii=False))
        return 0

    if target_engine == "postgres":
        cmd = [
            "pg_restore",
            "--clean",
            "--if-exists",
            "--no-owner",
            "--no-privileges",
            f"--dbname={target_url}",
            str(backup_file),
        ]
        run_command(cmd)
    else:
        target_path = sqlite_path_from_url(target_url)
        ensure_dir(target_path.parent)
        if target_path.exists():
            pre = target_path.with_suffix(target_path.suffix + f".pre_restore_{utc_now_stamp()}.bak")
            shutil.copy2(target_path, pre)
        shutil.copy2(backup_file, target_path)
        verify_sqlite_file(target_path)

    print(json.dumps({
        "status": "ok",
        "dry_run": False,
        "restored_backup": str(backup_file),
        "target_database_url_redacted": redact_url(target_url),
        "engine": target_engine,
    }, ensure_ascii=False))
    return 0


def command_post_restore_check(args: argparse.Namespace) -> int:
    database_url = (args.database_url or get_secret("DATABASE_URL") or "").strip()
    if not database_url:
        raise BackupToolError("Falta --database-url y DATABASE_URL no está definida.")

    engine = detect_engine(database_url)
    raw_expected = list(args.expect_table or [])
    if not raw_expected:
        raw_expected = list(CRITICAL_TABLES_DEFAULT)
    expected_tables = [t.strip() for t in raw_expected if t.strip()]
    for table in expected_tables:
        if not SAFE_IDENTIFIER.fullmatch(table):
            raise BackupToolError(f"Nombre de tabla inválido: {table}")

    missing: list[str] = []
    counts: dict[str, int | None] = {}

    if engine == "sqlite":
        db_path = sqlite_path_from_url(database_url)
        conn = sqlite3.connect(str(db_path))
        try:
            existing_rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
            existing = {str(r[0]) for r in existing_rows}
            for table in expected_tables:
                if table not in existing:
                    missing.append(table)
                    counts[table] = None
                else:
                    row = conn.execute(f"SELECT COUNT(*) FROM {table};").fetchone()
                    counts[table] = int(row[0] if row else 0)
        finally:
            conn.close()
    else:
        import sqlalchemy as sa

        engine_obj = sa.create_engine(database_url)
        with engine_obj.connect() as conn:
            existing_rows = conn.execute(sa.text("SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname='public'"))
            existing = {str(r[0]) for r in existing_rows}
            for table in expected_tables:
                if table not in existing:
                    missing.append(table)
                    counts[table] = None
                else:
                    row = conn.execute(sa.text(f"SELECT COUNT(*) FROM {table}"))
                    counts[table] = int(row.scalar() or 0)

    status = "ok" if not missing else "incomplete"
    out = {
        "status": status,
        "engine": engine,
        "database_url_redacted": redact_url(database_url),
        "expected_tables": expected_tables,
        "missing_tables": missing,
        "table_counts": counts,
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0 if not missing else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backup/restore operational tooling")
    sub = parser.add_subparsers(dest="command", required=True)

    p_backup_db = sub.add_parser("backup-db", help="Create DB backup")
    p_backup_db.add_argument("--database-url", default="", help="DB URL (default: DATABASE_URL env)")
    p_backup_db.add_argument("--output-dir", default="backups/db", help="Output directory")
    p_backup_db.add_argument("--prefix", default="app_web_db", help="Backup filename prefix")
    p_backup_db.add_argument("--no-verify", action="store_true", help="Skip integrity verification")
    p_backup_db.set_defaults(func=command_backup_db)

    p_backup_files = sub.add_parser("backup-files", help="Create tar.gz backup of critical files")
    p_backup_files.add_argument("--output-dir", default="backups/files", help="Output directory")
    p_backup_files.add_argument("--prefix", default="app_web_files", help="Bundle filename prefix")
    p_backup_files.add_argument("--include", action="append", default=[], help="Path to include. Can repeat")
    p_backup_files.set_defaults(func=command_backup_files)

    p_verify = sub.add_parser("verify-backup", help="Verify checksum and logical integrity")
    p_verify.add_argument("--backup-file", required=True, help="Backup file path")
    p_verify.add_argument("--checksum-file", default="", help="Checksum file path (.sha256)")
    p_verify.add_argument("--skip-integrity", action="store_true", help="Only verify checksum")
    p_verify.set_defaults(func=command_verify_backup)

    p_restore = sub.add_parser("restore-db", help="Restore DB from backup")
    p_restore.add_argument("--backup-file", required=True, help="Backup file path")
    p_restore.add_argument("--target-database-url", default="", help="Target DB URL (default: DATABASE_URL env)")
    p_restore.add_argument("--dry-run", action="store_true", help="Validate backup and target without restoring")
    p_restore.add_argument("--allow-prod", action="store_true", help="Allow restore when APP_ENV/FLASK_ENV=production")
    p_restore.set_defaults(func=command_restore_db)

    p_check = sub.add_parser("post-restore-check", help="Validate key tables after restore")
    p_check.add_argument("--database-url", default="", help="DB URL (default: DATABASE_URL env)")
    p_check.add_argument("--expect-table", action="append", default=[], help="Expected table. Can repeat")
    p_check.set_defaults(func=command_post_restore_check)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if hasattr(args, "no_verify"):
        args.verify = not bool(args.no_verify)

    try:
        return int(args.func(args))
    except BackupToolError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
