from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from app.services.agent_artifacts import (
    read_artifact_text,
    tool_output_should_externalize,
    write_tool_output_artifact,
)


class AgentArtifactsTests(unittest.TestCase):
    def test_tool_output_should_externalize_uses_byte_threshold(self) -> None:
        self.assertFalse(tool_output_should_externalize("x" * 10, threshold_bytes=20))
        self.assertTrue(tool_output_should_externalize("x" * 30, threshold_bytes=20))

    def test_write_and_read_tool_output_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"AI_CHAT_ARTIFACT_DIR": tmpdir}):
                blob_ref = write_tool_output_artifact(
                    run_id=12,
                    tool_call_ref="tc_1",
                    raw_output="artifact payload",
                )
                restored = read_artifact_text(blob_ref)

        self.assertTrue(blob_ref.startswith("tool_outputs/run-12/tc_1-"))
        self.assertEqual(restored, "artifact payload")


if __name__ == "__main__":
    unittest.main()
