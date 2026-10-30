from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from apply_core2_cpu_flags import PatchError, apply


class Core2CpuFlagsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "compiler_cpu_abi.gn"
        self.path.write_text(
            'if (current_cpu == "x64") {\n'
            '  cpu_abi_cflags += [ "-msse3" ]\n'
            '}\n',
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_applies_core2_flags_once(self) -> None:
        self.assertTrue(apply(self.path))
        content = self.path.read_text(encoding="utf-8")
        self.assertIn('  "-march=core2",', content)
        self.assertIn('  "-mtune=core2",', content)
        self.assertFalse(apply(self.path))
        self.assertEqual(content, self.path.read_text(encoding="utf-8"))

    def test_rejects_an_unrecognized_upstream_shape(self) -> None:
        self.path.write_text("cpu_abi_cflags += [ \"-m64\" ]\n", encoding="utf-8")
        with self.assertRaises(PatchError):
            apply(self.path)

    def test_rejects_partial_core2_flags(self) -> None:
        self.path.write_text(
            'cpu_abi_cflags += [ "-msse3" ]\n'
            'cpu_abi_cflags += [ "-march=core2" ]\n',
            encoding="utf-8",
        )
        with self.assertRaises(PatchError):
            apply(self.path)


if __name__ == "__main__":
    unittest.main()
