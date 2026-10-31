from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class Core2StageTest(unittest.TestCase):
    def test_build_uses_brave_cli_ninja_option_syntax(self) -> None:
        stage = (ROOT / "build/core2/stage.sh").read_text(encoding="utf-8")
        self.assertIn("pnpm run build Release --ninja j:2", stage)
        self.assertNotIn("--ninja jobs:2", stage)


if __name__ == "__main__":
    unittest.main()
