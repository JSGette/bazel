import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from baseline_resources import storage


class ResourceAccountingTest(unittest.TestCase):
    def test_storage_counts_hard_links_once_without_following_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data"
            data.write_bytes(b"x" * 8192)
            os.link(data, root / "hardlink")
            (root / "cycle").symlink_to(root, target_is_directory=True)
            result = storage({"root": root})["root"]
            expected = [root.lstat(), data.lstat(), (root / "cycle").lstat()]
            self.assertEqual(result["inodes"], 3)
            self.assertEqual(result["allocated_bytes"], sum(s.st_blocks * 512 for s in expected))
            self.assertEqual(result["apparent_bytes"], sum(s.st_size for s in expected))

    def test_child_cpu_includes_waited_grandchild_and_adopted_daemon(self):
        # Run in a separate process to avoid changing this test runner's reaping.
        script = '''
import json, os, time
from baseline_resources import cpu_snapshot, children
from run import enable_child_subreaper
assert enable_child_subreaper()["enabled"]
before = cpu_snapshot()
for detached in (False, True):
    pid = os.fork()
    if pid == 0:
        child = os.fork()
        if child == 0:
            start = time.process_time()
            while time.process_time() - start < .15:
                pass
            os._exit(0)
        if not detached:
            os.waitpid(child, 0)
        os._exit(0)
    os.waitpid(pid, 0)
    if detached:
        adopted, = children()
        os.waitpid(adopted, 0)
after = cpu_snapshot()
print(json.dumps({"seconds": sum(after["waited_descendants"].values()) - sum(before["waited_descendants"].values()), "children": children()}))
'''
        result = json.loads(subprocess.check_output([sys.executable, "-c", script],
                                                   cwd=Path(__file__).parent, timeout=15))
        self.assertGreaterEqual(result["seconds"], .28)
        self.assertEqual(result["children"], [])


if __name__ == "__main__":
    unittest.main()
