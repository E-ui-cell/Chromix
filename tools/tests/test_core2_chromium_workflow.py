from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import platform_pins


class Core2ChromiumWorkflowTest(unittest.TestCase):
    def test_workflow_uses_repository_linux_pin_and_staged_build(self) -> None:
        workflow = (ROOT / ".github/workflows/build-core2-chromium.yml").read_text(
            encoding="utf-8"
        )
        pins = platform_pins.load_pins(ROOT, "linux")

        self.assertEqual(pins["ChromiumVersion"], "155.0.8059.39")
        self.assertIn("uses: ./.github/workflows/build-posix-github.yml", workflow)
        self.assertIn("max-stages: 8", workflow)
        self.assertIn("core2_cpu: true", workflow)
        self.assertIn("use_upstream_cache: false", workflow)
        self.assertIn("artifact: chromix-linux-x64-core2", workflow)

    def test_reusable_posix_stage_applies_core2_flags_only_when_enabled(self) -> None:
        stage = (ROOT / "build/posix/ci-stage.sh").read_text(encoding="utf-8")
        reusable = (
            ROOT / ".github/workflows/build-posix-github.yml"
        ).read_text(encoding="utf-8")

        self.assertIn('case "${CHROMIX_CORE2_CPU:-0}" in', stage)
        self.assertIn('python3 "$REPO/tools/apply_core2_cpu_flags.py"', stage)
        self.assertIn("CHROMIX_CORE2_CPU: ${{ inputs.core2_cpu && '1' || '0' }}", reusable)


if __name__ == "__main__":
    unittest.main()
