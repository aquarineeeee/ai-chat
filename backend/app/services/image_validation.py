from __future__ import annotations

import asyncio
import hashlib
import io
import multiprocessing
import warnings
from dataclasses import dataclass
from pathlib import PurePosixPath

from PIL import Image

from app.core.config import get_settings
from app.core.exceptions import AppError


@dataclass(frozen=True)
class ValidatedImage:
    media_type: str
    width: int
    height: int
    sha256: str


def _decode_image(data: bytes, max_pixels: int, max_memory: int) -> tuple[str, int, int]:
    Image.MAX_IMAGE_PIXELS = max_pixels
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in {"JPEG", "PNG"}:
                raise ValueError("format")
            width, height = image.size
            if width <= 0 or height <= 0 or width * height > max_pixels:
                raise OverflowError("pixels")
            # Decoded image, decoder copies and process overhead.
            if width * height * 4 * 2 + len(data) * 2 + 32 * 1024 * 1024 > max_memory:
                raise OverflowError("memory")
            media_type = "image/jpeg" if image.format == "JPEG" else "image/png"
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            image.load()
    return media_type, width, height


def _decoder_child(connection, data: bytes, max_pixels: int, max_memory: int) -> None:
    try:
        connection.send(("ok", _decode_image(data, max_pixels, max_memory)))
    except (Image.DecompressionBombError, Image.DecompressionBombWarning, OverflowError):
        connection.send(("limit", None))
    except ValueError as exc:
        connection.send(("format" if str(exc) == "format" else "invalid", None))
    except Exception:
        connection.send(("invalid", None))
    finally:
        connection.close()


_decoding = 0


async def validate_image(data: bytes, filename: str, declared_type: str | None) -> ValidatedImage:
    global _decoding
    settings = get_settings()
    if len(data) > settings.image_max_bytes:
        raise AppError(413, "IMAGE_TOO_LARGE", "单张图片不能超过 1 MiB")
    if not data:
        raise AppError(400, "INVALID_IMAGE", "图片为空或无法解析")
    extension = PurePosixPath(filename.replace("\\", "/")).suffix.lower()
    expected = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}.get(extension)
    if expected is None or declared_type not in {expected, None, "application/octet-stream"}:
        raise AppError(415, "UNSUPPORTED_IMAGE", "仅支持文件类型一致的 JPG 和 PNG 图片")
    if _decoding >= settings.image_decode_concurrency:
        raise AppError(429, "IMAGE_DECODE_BUSY", "图片校验繁忙，请稍后重试")
    _decoding += 1
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=_decoder_child, args=(child, data, settings.image_max_pixels, settings.image_decode_max_memory_bytes), daemon=True)
    try:
        process.start()
        child.close()
        await asyncio.to_thread(process.join, settings.image_decode_timeout_seconds)
        if process.is_alive():
            process.terminate()
            await asyncio.to_thread(process.join)
            raise AppError(400, "IMAGE_DECODE_TIMEOUT", "图片校验超时，请换一张图片")
        if not parent.poll():
            raise AppError(400, "INVALID_IMAGE", "图片无法解析")
        status, result = parent.recv()
        if status == "limit":
            raise AppError(413, "IMAGE_PIXEL_LIMIT", "图片像素或解码内存超出上限")
        if status == "format":
            raise AppError(415, "UNSUPPORTED_IMAGE", "仅支持 JPG 和 PNG 图片")
        if status != "ok":
            raise AppError(400, "INVALID_IMAGE", "图片损坏或无法解析")
        media_type, width, height = result
        if media_type != expected:
            raise AppError(415, "UNSUPPORTED_IMAGE", "图片内容与文件扩展名不一致")
        return ValidatedImage(media_type, width, height, hashlib.sha256(data).hexdigest())
    finally:
        # Cancellation must finish the decoder before returning its concurrency slot.
        if process.pid is not None:
            if process.is_alive():
                process.terminate()
            process.join(timeout=2)
            if process.is_alive():
                process.kill()
                process.join()
            process.close()
        parent.close()
        child.close()
        _decoding -= 1
