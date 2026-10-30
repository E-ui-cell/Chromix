from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(shutil.which("zstd"), "zstd is required for snapshot tests")
class CiPartsStreamTest(unittest.TestCase):
    def test_stream_mode_produces_a_valid_snapshot_without_local_volumes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            work = base / "work"
            source = work / "src"
            source.mkdir(parents=True)
            (source / "keep.txt").write_text("ninja checkpoint\n", encoding="utf-8")
            snapshot = work / ".snapshot-stage-1"
            archive = base / "captured.tar.zst"
            streamer = base / "capture.sh"
            streamer.write_text(
                '#!/usr/bin/env bash\nset -euo pipefail\ncat > "$CAPTURE_ARCHIVE"\n',
                encoding="utf-8",
            )
            streamer.chmod(0o755)
            environment = dict(os.environ, CAPTURE_ARCHIVE=str(archive))

            subprocess.run(
                [
                    "bash",
                    str(ROOT / "build/posix/ci-parts.sh"),
                    str(work),
                    str(snapshot),
                    str(streamer),
                ],
                check=True,
                env=environment,
                capture_output=True,
                text=True,
            )

            decoded = subprocess.run(
                ["zstd", "-d", "-c", str(archive)],
                check=True,
                capture_output=True,
            ).stdout
            listing = subprocess.run(
                ["tar", "-tf", "-"],
                input=decoded,
                check=True,
                capture_output=True,
            ).stdout.decode()
            self.assertIn("./src/keep.txt", listing.splitlines())
            self.assertFalse(any(snapshot.rglob("tree.tar.zst.*")))


if __name__ == "__main__":
    unittest.main()
