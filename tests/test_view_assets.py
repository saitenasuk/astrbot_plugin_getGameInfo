import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIEW = ROOT / "pages" / "status"


class ViewAssetTests(unittest.TestCase):
    def test_view_entry_and_assets_exist(self):
        html = (VIEW / "index.html").read_text(encoding="utf-8")
        self.assertIn("./app.js", html)
        self.assertIn("./style.css", html)
        self.assertIn('type="module"', html)
        self.assertIn("<title>getGameInfo</title>", html)
        self.assertIn('id="push-check-button"', html)
        self.assertIn('data-tab="maintenance"', html)
        for name in (
            "app.js",
            "api.js",
            "ui.js",
            "style.css",
            "pages/overview.js",
            "pages/subscriptions.js",
            "pages/games.js",
            "pages/events.js",
            "pages/maintenance.js",
        ):
            self.assertTrue((VIEW / name).is_file(), name)

    def test_view_uses_bridge_instead_of_direct_fetch(self):
        api = (VIEW / "api.js").read_text(encoding="utf-8")
        app = (VIEW / "app.js").read_text(encoding="utf-8")
        self.assertIn("AstrBotPluginView", api)
        self.assertIn("AstrBotPluginPage", api)
        self.assertIn("apiGet", api)
        self.assertIn("subscriptions/test", app)
        self.assertIn("subscriptions/remove", app)
        self.assertIn("maintenance/seen", app)
        self.assertIn("diagnostics", app)
        self.assertIn("检查并推送", app)
        self.assertNotIn("localStorage", api + app)
        self.assertNotIn("document.cookie", api + app)

    def test_i18n_metadata_has_view_title(self):
        i18n = json.loads(
            (ROOT / ".astrbot-plugin" / "i18n" / "zh-CN.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(i18n["views"]["status"]["title"], "getGameInfo")
        self.assertEqual(i18n["pages"]["status"]["title"], "getGameInfo")
        self.assertTrue(i18n["views"]["status"]["description"])

    def test_script_files_use_relative_module_imports(self):
        app = (VIEW / "app.js").read_text(encoding="utf-8")
        self.assertIn('from "./api.js"', app)
        self.assertIn('from "./pages/overview.js"', app)
        self.assertNotIn("http://", app)
        self.assertNotIn("https://", app)
