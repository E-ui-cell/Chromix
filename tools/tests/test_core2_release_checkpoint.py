from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core2_release_checkpoint import (
    CheckpointError,
    build_manifest,
    release_tag,
    validate_manifest,
)


class Core2ReleaseCheckpointTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def make_volumes(self, count: int) -> list[Path]:
        volumes = []
        for index in range(1, count + 1):
            path = self.directory / f"tree.tar.zst.{index:03d}"
            path.write_bytes(b"checkpoint")
            volumes.append(path)
        return volumes

    def manifest(self, volumes: list[Path]) -> dict:
        return build_manifest(
            "E-ui-cell/Chromix",
            "123456",
            "2",
            "3",
            "v1.101.7",
            "a" * 40,
            volumes,
        )

    def test_builds_and_validates_numbered_checkpoint_manifest(self) -> None:
        manifest = self.manifest(self.make_volumes(2))
        assets = validate_manifest(
            manifest, "E-ui-cell/Chromix", "123456", "2", "3", "v1.101.7"
        )
        self.assertEqual(
            assets,
            [
                ("stage-03-tree.tar.zst.001", len(b"checkpoint")),
                ("stage-03-tree.tar.zst.002", len(b"checkpoint")),
            ],
        )
        self.assertEqual(release_tag("123456", "2"), "core2-brave-123456-attempt-2")

    def test_rejects_mismatched_checkpoint_identity(self) -> None:
        manifest = self.manifest(self.make_volumes(1))
        with self.assertRaisesRegex(CheckpointError, "run_id mismatch"):
            validate_manifest(
                manifest, "E-ui-cell/Chromix", "999999", "2", "3", "v1.101.7"
            )

    def test_rejects_noncontiguous_volume_manifest(self) -> None:
        manifest = self.manifest(self.make_volumes(2))
        manifest["volumes"][1]["asset"] = "stage-03-tree.tar.zst.003"
        with self.assertRaisesRegex(CheckpointError, "not contiguous"):
            validate_manifest(
                manifest, "E-ui-cell/Chromix", "123456", "2", "3", "v1.101.7"
            )


if __name__ == "__main__":
    unittest.main()
