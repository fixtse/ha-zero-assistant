"""Check the HACS package and README links without contacting a live HA instance."""
import json
from pathlib import Path
import re
import unittest
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parents[1]


class PackagingTests(unittest.TestCase):
    def test_hacs_layout_and_buttons(self):
        metadata = json.loads((ROOT / "hacs.json").read_text())
        self.assertTrue(metadata["name"])
        self.assertFalse(metadata["content_in_root"])
        domains = [path for path in (ROOT / "custom_components").iterdir() if path.is_dir() and not path.name.startswith("_")]
        self.assertEqual(len(domains), 1)
        component = domains[0]
        manifest = json.loads((component / "manifest.json").read_text())
        self.assertEqual(manifest["domain"], component.name)
        for key in ["name", "version", "documentation", "issue_tracker", "codeowners"]:
            self.assertTrue(manifest[key], key)
        self.assertTrue(manifest["config_flow"])
        self.assertTrue((component / "brand/icon.png").read_bytes().startswith(b"\x89PNG"))
        readme = (ROOT / "README.md").read_text()
        link = re.search(r"https://my\.home-assistant\.io/redirect/hacs_repository/\?[^)\s]+", readme).group()
        query = parse_qs(urlparse(link).query)
        self.assertEqual(query, {"owner": ["fixtse"], "repository": [ROOT.name], "category": ["integration"]})
        self.assertIn("config_flow_start/?domain=" + component.name, readme)
        self.assertIn("Install through HACS:", readme)
