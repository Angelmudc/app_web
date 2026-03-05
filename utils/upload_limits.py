# -*- coding: utf-8 -*-
from __future__ import annotations


def MAX_FILE_BYTES(app) -> int:
    try:
        cfg_bytes = int(app.config.get("APP_MAX_FILE_BYTES") or 0)
        if cfg_bytes > 0:
            return cfg_bytes
    except Exception:
        pass
    try:
        mb = float(app.config.get("APP_MAX_FILE_MB") or 3)
    except Exception:
        mb = 3
    if mb <= 0:
        mb = 3
    return int(mb * 1024 * 1024)


def human_size(nbytes: int | None) -> str:
    n = int(nbytes or 0)
    if n <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB"]
    size = float(n)
    idx = 0
    while size >= 1024.0 and idx < (len(units) - 1):
        size /= 1024.0
        idx += 1
    if idx == 0:
        return f"{int(size)} {units[idx]}"
    return f"{size:.1f} {units[idx]}"


def get_filestorage_size(file_storage) -> int | None:
    if not file_storage:
        return None

    # 1) content_length si viene del parser
    try:
        content_len = getattr(file_storage, "content_length", None)
        if content_len is not None:
            n = int(content_len)
            if n > 0:
                return n
    except Exception:
        pass

    # 2) fallback por stream seek/tell
    try:
        stream = getattr(file_storage, "stream", None)
        if stream is None:
            return None
        pos = stream.tell()
        stream.seek(0, 2)
        end = stream.tell()
        stream.seek(pos)
        if end >= 0:
            return int(end)
    except Exception:
        return None
    return None


def file_too_large(file_storage, max_bytes: int) -> bool:
    size = get_filestorage_size(file_storage)
    if size is None:
        return False
    return int(size) > int(max_bytes)
