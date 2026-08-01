from __future__ import annotations

import hashlib
import os
from pathlib import Path

from app.core.config import BASE_DIR


DEFAULT_TOOL_OUTPUT_BLOB_THRESHOLD_BYTES = 4096
_ARTIFACT_ROOT_ENV = "AI_CHAT_ARTIFACT_DIR"


def tool_output_blob_ref(
    *,
    run_id: int,
    tool_call_ref: str,
    sha256_hex: str,
) -> str:
    return f"tool_outputs/run-{run_id}/{tool_call_ref}-{sha256_hex[:12]}.txt"


def get_artifact_root() -> Path:
    configured = os.getenv(_ARTIFACT_ROOT_ENV, "").strip()
    if configured:
        return Path(configured)
    return BASE_DIR / ".artifacts"


def tool_output_should_externalize(raw_output: str, *, threshold_bytes: int = DEFAULT_TOOL_OUTPUT_BLOB_THRESHOLD_BYTES) -> bool:
    return len(raw_output.encode("utf-8")) > threshold_bytes


def write_tool_output_artifact(
    *,
    run_id: int,
    tool_call_ref: str,
    raw_output: str,
) -> str:
    sha256_hex = hashlib.sha256(raw_output.encode("utf-8")).hexdigest()
    blob_ref = tool_output_blob_ref(run_id=run_id, tool_call_ref=tool_call_ref, sha256_hex=sha256_hex)
    path = get_artifact_root() / blob_ref
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw_output, encoding="utf-8")
    return blob_ref


def read_artifact_text(blob_ref: str) -> str | None:
    if not blob_ref:
        return None
    path = get_artifact_root() / blob_ref
    if not path.exists() or not path.is_file():
        return None
    return path.read_text(encoding="utf-8")
