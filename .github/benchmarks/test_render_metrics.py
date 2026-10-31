import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

path = Path(__file__).with_name("render_metrics.py")
spec = importlib.util.spec_from_file_location("render_metrics", path)
metrics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(metrics)


class DiskMetricsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name)
        root = self.path / "123" / "root"
        (root / "data").mkdir(parents=True)
        (root / "tmp").mkdir()
        (self.path / "123" / "fd").mkdir()

    def test_named_files_and_missing_evidence(self):
        root = self.path / "123" / "root"
        (root / "data" / "audio").write_bytes(b"x" * 4137)
        (root / "tmp" / "cache").write_bytes(b"x" * 123)
        result = metrics.sample_disk(123, self.path)
        self.assertEqual(result["outcome"], "sampled")
        self.assertEqual(result["data"]["apparent_bytes"], 4137)
        self.assertEqual(result["tmp"]["apparent_bytes"], 123)
        self.assertEqual(result["unlinked_regular_bytes"], 0)
        missing = metrics.sample_disk(456, self.path)
        self.assertEqual(missing["outcome"], "partial")
        self.assertIsNone(missing["observed_allocated_bytes"])
        self.assertTrue(missing["errors"])

    @unittest.skipUnless(os.name == "posix", "POSIX unlinked descriptor accounting")
    def test_unlinked_descriptors_are_counted_once(self):
        descriptors = self.path / "123" / "fd"
        path = self.path / "private"
        with path.open("w+b") as file:
            file.write(b"x" * 8193)
            file.flush()
            path.unlink()
            target = Path("/proc") / str(os.getpid()) / "fd" / str(file.fileno())
            (descriptors / "1").symlink_to(target)
            (descriptors / "2").symlink_to(target)
            result = metrics.sample_disk(123, self.path)
            self.assertEqual(result["outcome"], "sampled")
            self.assertEqual(result["unlinked_regular_bytes"], 8193)
            self.assertEqual(result["unlinked_allocated_bytes"], os.fstat(file.fileno()).st_blocks * 512)


if __name__ == "__main__":
    unittest.main()
