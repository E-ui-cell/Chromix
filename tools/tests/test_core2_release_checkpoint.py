from __future__ import annotations

import argparse
import fnmatch
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core2_release_checkpoint import (
    CheckpointError,
    build_manifest,
    download_checkpoint,
    finalize_checkpoint,
    release_tag,
    stream_upload,
    validate_manifest,
)


class FakeGh:
    def __init__(self) -> None:
        self.releases: dict[str, dict[str, bytes]] = {}

    def run(self, command, **kwargs):
        args = command[1:]
        repo_index = args.index("--repo")
        args = args[:repo_index]
        if args[:2] == ["release", "view"]:
            tag = args[2]
            if tag not in self.releases:
                return subprocess.CompletedProcess(command, 1, "", "not found")
            if "--json" not in args:
                return subprocess.CompletedProcess(command, 0, "", "")
            selector = args[args.index("--jq") + 1]
            assets = self.releases[tag]
            if selector == ".assets":
                output = json.dumps(
                    [{"name": name, "size": len(data)} for name, data in assets.items()]
                )
            else:
                output = "\n".join(assets)
            return subprocess.CompletedProcess(command, 0, output, "")
        if args[:2] == ["release", "create"]:
            self.releases[args[2]] = {}
            return subprocess.CompletedProcess(command, 0, "", "")
        if args[:2] == ["release", "upload"]:
            tag = args[2]
            source = args[3]
            asset = Path(source).name
            self.releases[tag][asset] = Path(source).read_bytes()
            return subprocess.CompletedProcess(command, 0, "", "")
        if args[:2] == ["release", "download"]:
            tag = args[2]
            pattern = args[args.index("--pattern") + 1]
            destination = Path(args[args.index("--dir") + 1])
            matches = [
                (name, data)
                for name, data in self.releases[tag].items()
                if fnmatch.fnmatchcase(name, pattern)
            ]
            if not matches:
                return subprocess.CompletedProcess(command, 1, "", "no matching assets")
            for name, data in matches:
                (destination / name).write_bytes(data)
            return subprocess.CompletedProcess(command, 0, "", "")
        if args[:2] == ["release", "delete-asset"]:
            del self.releases[args[2]][args[3]]
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(f"unexpected gh command: {args}")


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
        metadata = [
            {
                "asset": f"stage-03-{path.name}",
                "size": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in volumes
        ]
        return build_manifest(
            "E-ui-cell/Chromix",
            "123456",
            "2",
            "3",
            "v1.101.7",
            "a" * 40,
            metadata,
            complete=True,
        )

    def test_builds_and_validates_numbered_checkpoint_manifest(self) -> None:
        manifest = self.manifest(self.make_volumes(2))
        assets = validate_manifest(
            manifest,
            "E-ui-cell/Chromix",
            "123456",
            "2",
            "3",
            "v1.101.7",
            require_complete=True,
        )
        self.assertEqual([asset["asset"] for asset in assets],
                         ["stage-03-tree.tar.zst.001", "stage-03-tree.tar.zst.002"])
        self.assertTrue(all(asset["size"] == len(b"checkpoint") for asset in assets))
        self.assertEqual(release_tag("123456", "2"), "core2-brave-123456-attempt-2")

    def test_rejects_mismatched_checkpoint_identity(self) -> None:
        manifest = self.manifest(self.make_volumes(1))
        with self.assertRaisesRegex(CheckpointError, "run_id mismatch"):
            validate_manifest(
                manifest,
                "E-ui-cell/Chromix",
                "999999",
                "2",
                "3",
                "v1.101.7",
                require_complete=True,
            )

    def test_rejects_noncontiguous_volume_manifest(self) -> None:
        manifest = self.manifest(self.make_volumes(2))
        manifest["volumes"][1]["asset"] = "stage-03-tree.tar.zst.003"
        with self.assertRaisesRegex(CheckpointError, "not contiguous"):
            validate_manifest(
                manifest,
                "E-ui-cell/Chromix",
                "123456",
                "2",
                "3",
                "v1.101.7",
                require_complete=True,
            )

    def test_rejects_provisional_manifest_for_resume(self) -> None:
        manifest = self.manifest(self.make_volumes(1))
        manifest["complete"] = False
        with self.assertRaisesRegex(CheckpointError, "not marked complete"):
            validate_manifest(
                manifest,
                "E-ui-cell/Chromix",
                "123456",
                "2",
                "3",
                "v1.101.7",
                require_complete=True,
            )

    def test_rejects_boolean_manifest_numbers(self) -> None:
        manifest = self.manifest(self.make_volumes(1))
        manifest["format"] = True
        with self.assertRaisesRegex(CheckpointError, "format has an invalid type"):
            validate_manifest(
                manifest,
                "E-ui-cell/Chromix",
                "123456",
                "2",
                "3",
                "v1.101.7",
                require_complete=True,
            )

    def test_rejects_checkpoint_larger_than_release_volume_limit(self) -> None:
        fake_gh = FakeGh()
        temporary = self.directory / "temporary"
        temporary.mkdir()
        args = argparse.Namespace(
            repo="E-ui-cell/Chromix",
            run_id="123456",
            attempt="2",
            stage="3",
            brave_core_ref="v1.101.7",
            workdir=self.directory,
            temp_dir=temporary,
        )
        with patch("core2_release_checkpoint.CHUNK_BYTES", 8), patch(
            "core2_release_checkpoint.MAX_VOLUMES", 3
        ), patch("core2_release_checkpoint.READ_BYTES", 3), patch(
            "core2_release_checkpoint.subprocess.run", side_effect=fake_gh.run
        ), patch.dict("os.environ", {"GH_TOKEN": "test-token"}), patch(
            "core2_release_checkpoint.sys.stdin",
            io.TextIOWrapper(io.BytesIO(b"0123456789abcdefghijklmno")),
        ):
            with self.assertRaisesRegex(CheckpointError, "exceeds the 3-volume"):
                stream_upload(args)
        assets = fake_gh.releases[release_tag("123456", "2")]
        self.assertEqual(len(assets), 3)
        self.assertNotIn("stage-03-manifest.json", assets)

    def test_streams_finalizes_and_restores_release_checkpoint(self) -> None:
        fake_gh = FakeGh()
        self.directory.joinpath(".core2-brave-core-commit").write_text(
            "a" * 40, encoding="utf-8"
        )
        temporary = self.directory / "temporary"
        temporary.mkdir()
        tag = release_tag("123456", "2")
        stream_args = argparse.Namespace(
            repo="E-ui-cell/Chromix",
            run_id="123456",
            attempt="2",
            stage="3",
            brave_core_ref="v1.101.7",
            workdir=self.directory,
            temp_dir=temporary,
        )
        with patch("core2_release_checkpoint.CHUNK_BYTES", 8), patch(
            "core2_release_checkpoint.MAX_VOLUMES", 3
        ), patch(
            "core2_release_checkpoint.READ_BYTES", 3
        ), patch("core2_release_checkpoint.subprocess.run", side_effect=fake_gh.run), patch.dict(
            "os.environ", {"GH_TOKEN": "test-token"}
        ), patch(
            "core2_release_checkpoint.sys.stdin",
            io.TextIOWrapper(io.BytesIO(b"0123456789abcdefghijklmn")),
        ):
            stream_upload(stream_args)
            manifest_name = "stage-03-manifest.json"
            self.assertIn(manifest_name, fake_gh.releases[tag])
            provisional = json.loads(fake_gh.releases[tag][manifest_name])
            self.assertFalse(provisional["complete"])
            self.assertEqual(len(provisional["volumes"]), 3)
            self.assertEqual(
                [name for name in fake_gh.releases[tag] if name.startswith("stage-03-tree")],
                [
                    "stage-03-tree.tar.zst.001",
                    "stage-03-tree.tar.zst.002",
                    "stage-03-tree.tar.zst.003",
                ],
            )

            finalize_checkpoint(
                argparse.Namespace(
                    repo="E-ui-cell/Chromix",
                    run_id="123456",
                    attempt="2",
                    stage="3",
                    brave_core_ref="v1.101.7",
                )
            )
            finalized = json.loads(fake_gh.releases[tag][manifest_name])
            self.assertTrue(finalized["complete"])

            destination = self.directory / "restored"
            download_checkpoint(
                argparse.Namespace(
                    repo="E-ui-cell/Chromix",
                    run_id="123456",
                    attempt="2",
                    stage="3",
                    brave_core_ref="v1.101.7",
                    destination=destination,
                )
            )
        restored = b"".join(
            (destination / f"tree.tar.zst.{index:03d}").read_bytes()
            for index in range(1, 4)
        )
        self.assertEqual(restored, b"0123456789abcdefghijklmn")


if __name__ == "__main__":
    unittest.main()
