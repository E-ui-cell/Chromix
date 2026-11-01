import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import platform_pins


class LinuxX64Core2OptionTest(unittest.TestCase):
    def test_149_workflow_starts_from_pinned_cache_without_external_checkpoint(self) -> None:
        workflow = (ROOT / ".github/workflows/core2-build-149-linux-x64.yml").read_text(
            encoding="utf-8"
        )
        pins = platform_pins.load_pins(ROOT, "linux")
        cache = json.loads((ROOT / "build/upstream-cache.json").read_text(encoding="utf-8"))

        self.assertEqual(pins["ChromiumVersion"], "149.0.7827.200")
        self.assertEqual(pins["LinuxChromiumVersion"], "149.0.7827.200")
        self.assertEqual(cache["sources"]["linux"]["chromium_version"], "149.0.7827.200")
        self.assertIn(
            "use_upstream_cache: ${{ github.event_name != 'workflow_dispatch' || inputs.use_upstream_cache }}",
            workflow,
        )
        self.assertNotIn("resume_run_id", workflow)
        self.assertNotIn("resume_tree_stage", workflow)
        self.assertNotIn("resume_artifact_ids", workflow)
        self.assertNotIn("resume_attempt", workflow)

    def test_dedicated_workflow_builds_pinned_chromium_for_core2(self) -> None:
        workflow = (ROOT / ".github/workflows/core2-build-linux-x64.yml").read_text(
            encoding="utf-8"
        )
        pins = platform_pins.load_pins(ROOT, "linux")

        self.assertEqual(pins["ChromiumVersion"], pins["LinuxChromiumVersion"])
        self.assertIn("uses: ./.github/workflows/build-posix-github.yml", workflow)
        self.assertIn("max-stages: ${{ inputs.build_mode == 'single' && 1 || 8 }}", workflow)
        self.assertIn("core2_cpu: true", workflow)
        self.assertIn("use_upstream_cache: ${{ github.event_name != 'workflow_dispatch' || inputs.use_upstream_cache }}", workflow)
        self.assertIn("artifact: chromix-linux-x64", workflow)
        self.assertIn("resume_artifact_ids: ${{ inputs.resume_artifact_ids || '' }}", workflow)

    def test_reusable_posix_stage_applies_core2_flags_only_when_enabled(self) -> None:
        stage = (ROOT / "build/posix/ci-stage.sh").read_text(encoding="utf-8")
        reusable = (
            ROOT / ".github/workflows/build-posix-github.yml"
        ).read_text(encoding="utf-8")
        linux = (ROOT / ".github/workflows/build-linux-x64.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn('case "${CHROMIX_CORE2_CPU:-0}" in', stage)
        self.assertIn('python3 "$REPO/tools/apply_core2_cpu_flags.py"', stage)
        self.assertIn("CHROMIX_CORE2_CPU: ${{ inputs.core2_cpu && '1' || '0' }}", reusable)
        self.assertIn("core2_cpu: ${{ inputs.core2_cpu }}", linux)


if __name__ == "__main__":
    unittest.main()
