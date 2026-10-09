from __future__ import annotations

import logging
import os
import shutil
import tempfile
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Protocol
from uuid import uuid4

from app.core.config import BASE_DIR, get_settings
from app.core.exceptions import AppError

logger = logging.getLogger(__name__)


class Storage(Protocol):
    def put_file(self, user_id: int, media_type: str, data: bytes) -> str: ...
    def open_file(self, relative_path: str) -> BinaryIO: ...
    def delete_file(self, relative_path: str) -> None: ...


class LocalFileStorage:
    def __init__(self, root: str | Path, min_free_bytes: int = 268435456):
        configured = Path(root)
        self.root = (configured if configured.is_absolute() else BASE_DIR / configured).resolve()
        self.min_free_bytes = min_free_bytes

    def initialize(self) -> None:
        for static_root in (BASE_DIR / "static", BASE_DIR.parent / "static"):
            if self.root.is_relative_to(static_root.resolve()):
                raise AppError(500, "STORAGE_ERROR", "图片上传目录不能位于公开静态目录内")
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            temp_dir = self.resolve_path(".tmp")
            temp_dir.mkdir(exist_ok=True)
            with tempfile.TemporaryFile(dir=temp_dir) as probe:
                probe.write(b"ok")
        except OSError as exc:
            raise self._error(exc) from exc

    def resolve_path(self, relative_path: str) -> Path:
        parts = PurePosixPath(relative_path)
        if not relative_path or parts.is_absolute() or "\\" in relative_path or ":" in relative_path or any(p in {".", ".."} for p in parts.parts):
            raise AppError(500, "STORAGE_ERROR", "图片存储路径无效")
        candidate = (self.root / Path(*parts.parts)).resolve()
        if not candidate.is_relative_to(self.root) or candidate == self.root:
            raise AppError(500, "STORAGE_ERROR", "图片存储路径无效")
        return candidate

    @staticmethod
    def _error(exc: OSError) -> AppError:
        logger.error("Image storage operation failed (%s)", type(exc).__name__)
        return AppError(500, "STORAGE_ERROR", "图片存储暂不可用，请稍后重试")

    def put_file(self, user_id: int, media_type: str, data: bytes) -> str:
        if user_id <= 0 or media_type not in {"image/jpeg", "image/png"}:
            raise AppError(500, "STORAGE_ERROR", "图片存储参数无效")
        extension = "jpg" if media_type == "image/jpeg" else "png"
        relative_path = f"users/{user_id}/{uuid4().hex}.{extension}"
        target = self.resolve_path(relative_path)
        temp_path: Path | None = None
        try:
            self.initialize()
            if shutil.disk_usage(self.root).free < self.min_free_bytes + len(data):
                raise AppError(507, "STORAGE_FULL", "图片存储空间不足")
            target.parent.mkdir(parents=True, exist_ok=True)
            # Temp and destination share a filesystem; replace is atomic.
            with tempfile.NamedTemporaryFile(dir=self.resolve_path(".tmp"), delete=False) as handle:
                temp_path = Path(handle.name)
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, target)
            return relative_path
        except OSError as exc:
            raise self._error(exc) from exc
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    logger.error("Image temporary-file cleanup failed")

    def open_file(self, relative_path: str) -> BinaryIO:
        try:
            return self.resolve_path(relative_path).open("rb")
        except FileNotFoundError as exc:
            raise AppError(404, "ATTACHMENT_FILE_MISSING", "图片文件已丢失，请移除引用后重试") from exc
        except OSError as exc:
            raise self._error(exc) from exc

    def delete_file(self, relative_path: str) -> None:
        try:
            self.resolve_path(relative_path).unlink(missing_ok=True)
        except OSError as exc:
            raise self._error(exc) from exc


@lru_cache
def get_storage() -> LocalFileStorage:
    settings = get_settings()
    return LocalFileStorage(settings.local_upload_dir, settings.upload_min_free_bytes)
