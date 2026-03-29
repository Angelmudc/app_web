#!/usr/bin/env python3
"""Ship latest backup artifacts to external storage (S3/GCS/local fs)."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

FORBIDDEN_BASENAMES = {".env", "clave1.json", "service_account.json"}
ALLOWED_EXTENSIONS = {
    ".dump",
    ".sqlite",
    ".db",
    ".tar.gz",
    ".sha256",
    ".metadata.json",
}


class ShipError(RuntimeError):
    pass


@dataclass
class ArtifactGroup:
    primary: Path
    checksum: Path
    metadata: Path

    def files(self) -> list[Path]:
        return [self.primary, self.checksum, self.metadata]


class RemoteSink:
    def upload_file(self, local_path: Path, remote_key: str, *, forbid_overwrite: bool) -> dict:
        raise NotImplementedError


class LocalFilesystemSink(RemoteSink):
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def upload_file(self, local_path: Path, remote_key: str, *, forbid_overwrite: bool) -> dict:
        target = self.root / remote_key
        target.parent.mkdir(parents=True, exist_ok=True)
        if forbid_overwrite and target.exists():
            raise ShipError(f"Objeto ya existe en destino local: {target}")

        shutil.copy2(local_path, target)
        if target.stat().st_size != local_path.stat().st_size:
            raise ShipError(f"Tamaño no coincide tras copia local: {target}")

        return {
            "backend": "local_fs",
            "target": str(target),
            "bytes": int(target.stat().st_size),
        }


class S3Sink(RemoteSink):
    def __init__(self, bucket: str, object_lock_mode: str = "", object_lock_days: int = 0):
        if not bucket:
            raise ShipError("BACKUP_REMOTE_BUCKET es obligatorio para S3.")

        try:
            import boto3
            from botocore.exceptions import ClientError
        except Exception as exc:
            raise ShipError("Falta dependencia boto3 para usar S3.") from exc

        self._ClientError = ClientError
        self.client = boto3.client("s3")
        self.bucket = bucket
        self.object_lock_mode = (object_lock_mode or "").strip().upper()
        self.object_lock_days = max(0, int(object_lock_days or 0))

    def _exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except self._ClientError as exc:
            code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise

    def upload_file(self, local_path: Path, remote_key: str, *, forbid_overwrite: bool) -> dict:
        if forbid_overwrite and self._exists(remote_key):
            raise ShipError(f"Objeto ya existe en S3: s3://{self.bucket}/{remote_key}")

        self.client.upload_file(str(local_path), self.bucket, remote_key)

        # Optional object lock (bucket must have Object Lock enabled).
        if self.object_lock_mode in {"GOVERNANCE", "COMPLIANCE"} and self.object_lock_days > 0:
            retain_until = datetime.now(timezone.utc) + timedelta(days=self.object_lock_days)
            self.client.put_object_retention(
                Bucket=self.bucket,
                Key=remote_key,
                Retention={
                    "Mode": self.object_lock_mode,
                    "RetainUntilDate": retain_until,
                },
            )

        head = self.client.head_object(Bucket=self.bucket, Key=remote_key)
        remote_size = int(head.get("ContentLength") or 0)
        local_size = int(local_path.stat().st_size)
        if remote_size != local_size:
            raise ShipError(
                f"Tamaño remoto no coincide en S3 para {remote_key}: remote={remote_size} local={local_size}"
            )

        return {
            "backend": "s3",
            "target": f"s3://{self.bucket}/{remote_key}",
            "bytes": remote_size,
        }


class GCSSink(RemoteSink):
    def __init__(self, bucket: str):
        if not bucket:
            raise ShipError("BACKUP_REMOTE_BUCKET es obligatorio para GCS.")

        try:
            from google.cloud import storage
        except Exception as exc:
            raise ShipError("Falta dependencia google-cloud-storage para usar GCS.") from exc

        self.client = storage.Client()
        self.bucket_name = bucket
        self.bucket = self.client.bucket(bucket)

    def upload_file(self, local_path: Path, remote_key: str, *, forbid_overwrite: bool) -> dict:
        blob = self.bucket.blob(remote_key)
        if forbid_overwrite and blob.exists(client=self.client):
            raise ShipError(f"Objeto ya existe en GCS: gs://{self.bucket_name}/{remote_key}")

        blob.upload_from_filename(str(local_path))
        blob.reload(client=self.client)

        remote_size = int(blob.size or 0)
        local_size = int(local_path.stat().st_size)
        if remote_size != local_size:
            raise ShipError(
                f"Tamaño remoto no coincide en GCS para {remote_key}: remote={remote_size} local={local_size}"
            )

        return {
            "backend": "gcs",
            "target": f"gs://{self.bucket_name}/{remote_key}",
            "bytes": remote_size,
        }


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_date_prefix(now: datetime) -> str:
    return now.strftime("%Y/%m/%d")


def _allowed_artifact(path: Path) -> bool:
    name = path.name
    if name in FORBIDDEN_BASENAMES:
        return False

    for suffix in ALLOWED_EXTENSIONS:
        if name.endswith(suffix):
            return True
    return False


def _list_candidates(dir_path: Path, primary_suffixes: Iterable[str]) -> list[Path]:
    if not dir_path.exists():
        return []

    candidates = []
    for item in dir_path.iterdir():
        if not item.is_file():
            continue
        if any(item.name.endswith(sfx) for sfx in primary_suffixes):
            candidates.append(item)

    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates


def _artifact_group_for(primary: Path) -> ArtifactGroup:
    checksum = primary.with_suffix(primary.suffix + ".sha256")
    metadata = primary.with_suffix(primary.suffix + ".metadata.json")
    if not checksum.exists() or not metadata.exists():
        raise ShipError(f"Artefacto incompleto para {primary.name} (faltan .sha256 o .metadata.json)")

    return ArtifactGroup(primary=primary, checksum=checksum, metadata=metadata)


def find_latest_artifacts(db_dir: Path, files_dir: Path) -> list[ArtifactGroup]:
    db_candidates = _list_candidates(db_dir, [".dump", ".sqlite", ".db"])
    files_candidates = _list_candidates(files_dir, [".tar.gz"])

    groups: list[ArtifactGroup] = []

    if db_candidates:
        groups.append(_artifact_group_for(db_candidates[0]))
    else:
        raise ShipError(f"No se encontró backup de DB en {db_dir}")

    if files_candidates:
        groups.append(_artifact_group_for(files_candidates[0]))
    else:
        raise ShipError(f"No se encontró backup de archivos en {files_dir}")

    return groups


def build_sink(provider: str, *, bucket: str, local_dir: str, s3_lock_mode: str, s3_lock_days: int) -> RemoteSink:
    p = (provider or "").strip().lower()
    if p == "s3":
        return S3Sink(bucket=bucket, object_lock_mode=s3_lock_mode, object_lock_days=s3_lock_days)
    if p == "gcs":
        return GCSSink(bucket=bucket)
    if p == "local_fs":
        root = Path(local_dir).resolve()
        return LocalFilesystemSink(root)
    raise ShipError("Provider inválido. Usa s3, gcs o local_fs.")


def ship_latest(args: argparse.Namespace) -> int:
    provider = (args.provider or os.getenv("BACKUP_REMOTE_PROVIDER") or "").strip().lower()
    if not provider:
        raise ShipError("Define --provider o BACKUP_REMOTE_PROVIDER (s3|gcs|local_fs).")

    db_dir = Path(args.db_dir).resolve()
    files_dir = Path(args.files_dir).resolve()
    bucket = (args.bucket or os.getenv("BACKUP_REMOTE_BUCKET") or "").strip()
    local_remote_dir = (args.local_remote_dir or os.getenv("BACKUP_REMOTE_LOCAL_DIR") or "").strip()

    sink = build_sink(
        provider,
        bucket=bucket,
        local_dir=local_remote_dir,
        s3_lock_mode=(args.s3_lock_mode or os.getenv("S3_OBJECT_LOCK_MODE") or ""),
        s3_lock_days=int(args.s3_lock_days or os.getenv("S3_OBJECT_LOCK_DAYS") or 0),
    )

    env_name = (args.environment or os.getenv("APP_ENV") or os.getenv("FLASK_ENV") or "prod").strip().lower()
    host = (args.host_label or socket.gethostname() or "host").strip().lower().replace(" ", "-")
    prefix = (args.remote_prefix or os.getenv("BACKUP_REMOTE_PREFIX") or "app_web/backups").strip("/")
    date_prefix = utc_date_prefix(utc_now())
    forbid_overwrite = not bool(args.allow_overwrite)

    groups = find_latest_artifacts(db_dir=db_dir, files_dir=files_dir)

    uploads = []
    total_bytes = 0

    for group in groups:
        for file_path in group.files():
            if not _allowed_artifact(file_path):
                raise ShipError(f"Archivo no permitido para upload: {file_path}")
            if file_path.stat().st_size <= 0:
                raise ShipError(f"Archivo vacío, upload cancelado: {file_path}")

            remote_key = f"{prefix}/{env_name}/{host}/{date_prefix}/{file_path.name}"

            if args.dry_run:
                result = {
                    "backend": provider,
                    "target": remote_key,
                    "bytes": int(file_path.stat().st_size),
                    "dry_run": True,
                }
            else:
                result = sink.upload_file(file_path, remote_key, forbid_overwrite=forbid_overwrite)

            uploads.append(
                {
                    "local_path": str(file_path),
                    "remote": result,
                }
            )
            total_bytes += int(file_path.stat().st_size)

    manifest = {
        "status": "ok",
        "provider": provider,
        "executed_at_utc": utc_now().isoformat(),
        "environment": env_name,
        "host": host,
        "prefix": prefix,
        "uploaded_files": uploads,
        "uploaded_count": len(uploads),
        "uploaded_total_bytes": total_bytes,
        "overwrite_allowed": not forbid_overwrite,
        "dry_run": bool(args.dry_run),
    }

    print(json.dumps(manifest, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ship latest backup artifacts to remote storage")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ship = sub.add_parser("ship-latest", help="Upload latest DB + files artifacts")
    p_ship.add_argument("--provider", default="", help="s3|gcs|local_fs (default BACKUP_REMOTE_PROVIDER)")
    p_ship.add_argument("--db-dir", default="backups/db", help="Directory containing DB backup artifacts")
    p_ship.add_argument("--files-dir", default="backups/files", help="Directory containing file backup artifacts")
    p_ship.add_argument("--bucket", default="", help="Remote bucket name for s3/gcs")
    p_ship.add_argument("--remote-prefix", default="", help="Remote path prefix")
    p_ship.add_argument("--environment", default="", help="Environment label")
    p_ship.add_argument("--host-label", default="", help="Host label override")
    p_ship.add_argument("--local-remote-dir", default="", help="Root directory for local_fs backend")
    p_ship.add_argument("--s3-lock-mode", default="", help="S3 object lock mode: GOVERNANCE|COMPLIANCE")
    p_ship.add_argument("--s3-lock-days", type=int, default=0, help="S3 object lock retention days")
    p_ship.add_argument("--allow-overwrite", action="store_true", help="Allow overwrite of remote objects")
    p_ship.add_argument("--dry-run", action="store_true", help="Do not upload; only print actions")
    p_ship.set_defaults(func=ship_latest)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args))
    except ShipError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
