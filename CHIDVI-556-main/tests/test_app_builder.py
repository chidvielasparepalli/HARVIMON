import unittest

from core.app_builder.engine import _safe_rel, _slug, _strip_fences


class AppBuilderUtilityTests(unittest.TestCase):
    def test_slug(self):
        self.assertEqual(_slug("Luxury AI / Builder!"), "luxury-ai-builder")

    def test_safe_relative_path(self):
        self.assertEqual(_safe_rel("app/page.tsx"), "app/page.tsx")
        with self.assertRaises(ValueError):
            _safe_rel("../escape.txt")

    def test_strip_fences(self):
        fenced = chr(96) * 3 + "json\n{\"ok\": true}\n" + chr(96) * 3
        self.assertEqual(_strip_fences(fenced), '{\"ok\": true}')


if __name__ == "__main__":
    unittest.main()
