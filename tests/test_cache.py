import os
import tempfile
import threading
import unittest
from pathlib import Path

os.environ["KEYFIT_CONFIG_DIR"] = tempfile.mkdtemp()  # tests never touch the real ~/.config/keyfit

from keyfit import cache  # noqa: E402


class CacheTests(unittest.TestCase):
    def setUp(self):
        os.environ["KEYFIT_CONFIG_DIR"] = tempfile.mkdtemp()

    def test_parallel_writes_do_not_raise(self):
        errors = []

        def write(i):
            try:
                for j in range(20):
                    cache.put(f"k{i}-{j}", "ok", "confirmed")
            except Exception as err:  # the race this guards against raised FileNotFoundError
                errors.append(err)

        threads = [threading.Thread(target=write, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertIsNotNone(cache.get("k0-19"))

    def test_fresh_entries_only(self):
        cache.put("a", "ok", "x")
        self.assertIsNotNone(cache.get("a"))
        self.assertIsNone(cache.get("a", now=10 ** 12))

    def test_unwritable_cache_never_breaks_a_launch(self):
        os.environ["KEYFIT_CONFIG_DIR"] = "/dev/null/keyfit"
        cache.put("a", "ok", "x")  # must not raise
        self.assertIsNone(cache.get("a"))

    def test_no_temp_files_left_behind(self):
        cache.put("a", "ok", "x")
        folder = Path(os.environ["KEYFIT_CONFIG_DIR"]) / "cache"
        self.assertEqual([p.name for p in folder.iterdir()], ["checks.json"])


if __name__ == "__main__":
    unittest.main()
