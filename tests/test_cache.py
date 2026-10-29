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
        cache.drop("a")
        self.assertIsNone(cache.get("a"))

    def test_a_file_of_another_shape_counts_as_empty(self):
        # KEY-82: a hand-edited cache (`[]`, `null`) stopped every tool with "internal error" until it was deleted
        path = Path(os.environ["KEYFIT_CONFIG_DIR"]) / "cache" / "checks.json"
        path.parent.mkdir()
        for text in ("[]", "null", '"x"', '{"a": 5}', '{"a": {"at": "soon", "status": "ok", "detail": "x"}}',
                     '{"a": {"at": 1e12, "status": "great", "detail": "x"}}', '{"a": {"at": 1e12, "status": "ok"}}'):
            with self.subTest(text=text):
                path.write_text(text)
                self.assertIsNone(cache.get("a", now=1e12))
                cache.drop("a")
                cache.put("b", "ok", "y")
                self.assertEqual(cache.get("b")["detail"], "y")

    def test_drop_forgets_one_entry(self):
        cache.put("a", "ok", "x")
        cache.put("b", "ok", "y")
        cache.drop("a")
        self.assertIsNone(cache.get("a"))
        self.assertEqual(cache.get("b")["detail"], "y")

    def test_drop_writes_nothing_when_there_is_nothing_to_forget(self):
        cache.drop("a")
        self.assertFalse((Path(os.environ["KEYFIT_CONFIG_DIR"]) / "cache").exists())

    def test_cache_and_registry_are_owner_only_from_the_first_byte(self):
        from keyfit.registry import Registry, write_private

        old = os.umask(0)  # nothing but write_private's own mode decides
        try:
            cache.put("a", "ok", "x")
            Registry(Path(os.environ["KEYFIT_CONFIG_DIR"]) / "registry.toml").save()
            stale = Path(os.environ["KEYFIT_CONFIG_DIR"]) / "stale.tmp"
            stale.write_text("old")
            stale.chmod(0o644)  # left behind by an earlier crash: O_CREAT keeps its mode
            write_private(stale, "new")
        finally:
            os.umask(old)
        root = Path(os.environ["KEYFIT_CONFIG_DIR"])
        modes = {p.name: p.stat().st_mode & 0o777 for p in (root / "cache", root / "cache" / "checks.json",
                                                            root / "registry.toml", stale)}
        self.assertEqual(modes, {"cache": 0o700, "checks.json": 0o600, "registry.toml": 0o600, "stale.tmp": 0o600})

    def test_no_temp_files_left_behind(self):
        cache.put("a", "ok", "x")
        folder = Path(os.environ["KEYFIT_CONFIG_DIR"]) / "cache"
        self.assertEqual([p.name for p in folder.iterdir()], ["checks.json"])


if __name__ == "__main__":
    unittest.main()
