import shutil
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.dont_write_bytecode = True

import derive_beta  # noqa: E402
import sync_versions as sv  # noqa: E402

REVISION = "bdeee704d4f21cd9eaac667628628a933f8969cb"


class PureFunctionsTest(unittest.TestCase):
    def test_newest_compares_numbers_not_text(self):
        self.assertEqual(sv.newest(["v1.0.9", "v1.0.30", "v1.0.4", "latest"], r"^v(\d+\.\d+\.\d+)$"), "1.0.30")

    def test_newest_separates_the_beta_line(self):
        tags = ["v2.94.1", "v2.94.1-beta", "v2.97.0-beta"]
        self.assertEqual(sv.newest(tags, r"^v(\d+\.\d+\.\d+)$"), "2.94.1")
        self.assertEqual(sv.newest(tags, r"^v(\d+\.\d+\.\d+-beta)$"), "2.97.0-beta")

    def test_beta_version_is_the_scheme_of_the_home_assistant_add_ons(self):
        self.assertEqual(sv.beta_version("1.0.34", "2026-11-03T16:26:08.392786629Z"), "1.0.34-beta.202611031626")

    def test_a_new_release_outranks_older_beta_builds(self):
        self.assertGreater(sv.version_key(sv.beta_version("1.0.35", "2026-11-01T00:00:00Z")),
                           sv.version_key(sv.beta_version("1.0.34", "2026-11-03T00:00:00Z")))

    def test_release_entry_links_the_release_notes(self):
        text = sv.entry("1.0.34", "o/r", {})
        self.assertTrue(text.startswith("### 1.0.34\n"))
        self.assertIn("https://github.com/o/r/releases/tag/v1.0.34", text)

    def test_beta_entry_links_what_changed_since_the_release(self):
        text = sv.entry("1.0.34-beta.202611031626", "o/r", {"branch": "beta"}, {"created": "2026-11-03T16:26:08Z", "revision": REVISION}, "1.0.34")
        self.assertIn("2026-11-03 16:26 UTC", text)
        self.assertIn(f"https://github.com/o/r/compare/v1.0.34...{REVISION}", text)

    def test_beta_entry_without_a_revision_links_the_branch(self):
        text = sv.entry("1.0.0-beta.202611031626", "o/r", {"branch": "release/v26-beta"}, {"created": "2026-11-03T16:26:08Z", "revision": ""}, "1.0.0")
        self.assertIn("https://github.com/o/r/commits/release/v26-beta", text)

    def test_entries_are_newest_first_and_capped(self):
        template = "<Container>\n  <Date>2026-10-28</Date>\n</Container>\n"
        for minor in range(sv.KEEP + 3):
            template = sv.with_entry(template, sv.entry(f"1.{minor}.0", "o/r", {}))
        self.assertEqual(sv.recorded(template), f"1.{sv.KEEP + 2}.0")
        self.assertEqual(len(sv.entries(template)), sv.KEEP)
        self.assertEqual(template.count("<Changes>"), 1)
        ET.fromstring(template)                                     # still well-formed

    def test_a_template_without_changes_records_nothing(self):
        self.assertIsNone(sv.recorded("<Container>\n  <Date>2026-10-28</Date>\n</Container>\n"))


class SyncTest(unittest.TestCase):
    """sync() against a copy of the repository, with the network replaced by fakes."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        for item in ROOT.iterdir():
            if item.name not in (".git", "__pycache__"):
                (shutil.copytree if item.is_dir() else shutil.copy)(item, self.tmp / item.name)
        # The tests start from fixed versions, not from what the repository records: that is raised every few hours.
        for path in (self.tmp / "templates").glob("*.xml"):
            path.write_text(derive_beta.CHANGES.sub("", path.read_text(encoding="utf-8")), encoding="utf-8")
        self.record("doorbell-map", sv.entry("1.0.34", "helmerzNL/Doorbell-Map", {}))
        self.record("doorbell-map-beta", sv.entry("1.0.34-beta.202611031626", "helmerzNL/Doorbell-Map", {}, {"created": "2026-11-03T16:26:00Z"}, "1.0.34"))
        self.tags = {"helmerzNL/DiscVault": ["v26.10.17"], "helmerzNL/Doorbell-Map": ["v1.0.34", "v1.0.35"],
                     "helmerzNL/MealieShopper": ["v2.94.1", "v2.98.0-beta"], "Flux76HQ/3dprint_pricing": ["v0.26.23"],
                     "helmerzNL/NAS-Storage-Analyzer": ["v0.2.1"]}
        self.published = {"ghcr.io/helmerznl/discvault:26.10.17", "ghcr.io/helmerznl/doorbell-map:v1.0.35", "ghcr.io/helmerznl/mealieshopper:2.94.1",
                          "ghcr.io/helmerznl/mealieshopper:2.98.0-beta", "ghcr.io/flux76hq/3dprint_pricing:0.26.23",
                          "ghcr.io/helmerznl/nas-storage-analyzer-aio:0.2.1"}
        self.builds = {"ghcr.io/helmerznl/discvault:beta": "2026-11-04T08:00:00Z", "ghcr.io/helmerznl/doorbell-map:beta": "2026-11-04T09:15:00Z",
                       "ghcr.io/flux76hq/3dprint_pricing:beta": "2026-11-04T10:30:00Z",
                       "ghcr.io/helmerznl/nas-storage-analyzer-aio:beta": "2026-11-05T13:00:00Z"}

    def record(self, name, text):
        path = self.tmp / "templates" / f"{name}.xml"
        path.write_text(sv.with_entry(path.read_text(encoding="utf-8"), text), encoding="utf-8")

    def run_sync(self, write=True):
        return sv.sync(self.tmp, write, tags_of=self.tags.__getitem__, exists=self.published.__contains__,
                       build_of=lambda ref: {"created": self.builds[ref], "revision": REVISION} if ref in self.builds else None)

    def template(self, name):
        return (self.tmp / "templates" / f"{name}.xml").read_text(encoding="utf-8")

    def test_records_new_versions_in_stable_and_beta(self):
        report = self.run_sync()
        self.assertIn("doorbell-map: 1.0.34 -> 1.0.35", report)
        self.assertEqual(sv.recorded(self.template("doorbell-map")), "1.0.35")
        self.assertEqual(sv.recorded(self.template("doorbell-map-beta")), "1.0.35-beta.202611040915")
        self.assertEqual(sv.recorded(self.template("mealieshopper-beta")), "2.98.0-beta")
        self.assertEqual(sv.recorded(self.template("printpricing-beta")), "0.26.23-beta.202611041030")

    def test_the_beta_templates_still_match_after_a_sync(self):
        self.run_sync()
        self.assertEqual(derive_beta.differences(self.tmp), [])

    def test_the_templates_stay_in_order(self):
        import check_templates
        self.run_sync()
        self.assertEqual(check_templates.check_repository(self.tmp), [])

    def test_check_writes_nothing(self):
        before = self.template("doorbell-map")
        self.assertTrue(self.run_sync(write=False))
        self.assertEqual(self.template("doorbell-map"), before)

    def test_a_release_without_its_image_is_not_recorded(self):
        self.published.discard("ghcr.io/helmerznl/doorbell-map:v1.0.35")
        report = self.run_sync()
        self.assertIn("doorbell-map: 1.0.35 is released, but the image ghcr.io/helmerznl/doorbell-map:v1.0.35 is not published yet; staying on 1.0.34", report)
        self.assertEqual(sv.recorded(self.template("doorbell-map")), "1.0.34")

    def test_a_missing_beta_image_is_reported(self):
        del self.builds["ghcr.io/helmerznl/doorbell-map:beta"]
        self.assertTrue(any(line.startswith("doorbell-map-beta: the image ghcr.io/helmerznl/doorbell-map:beta is not published") for line in self.run_sync()))

    def test_an_older_or_equal_version_changes_nothing(self):
        self.run_sync()
        self.assertEqual(self.run_sync(), [])
        self.builds["ghcr.io/helmerznl/doorbell-map:beta"] = "2026-11-01T00:00:00Z"        # an older build than the recorded one
        self.assertEqual(self.run_sync(), [])


class DeriveKeepsTheBetaChangesTest(unittest.TestCase):
    def setUp(self):
        self.stable = (ROOT / "templates" / "doorbell-map.xml").read_text(encoding="utf-8")
        self.source = derive_beta.sources()["doorbell-map"]

    def test_the_beta_does_not_take_the_stable_changes(self):
        stable = sv.with_entry(self.stable, sv.entry("9.9.9", "o/r", {}))
        beta = derive_beta.beta_template(stable, "doorbell-map", self.source)
        self.assertNotIn("9.9.9", beta)

    def test_the_beta_keeps_its_own_changes(self):
        current = sv.with_entry(derive_beta.beta_template(self.stable, "doorbell-map", self.source), sv.entry("8.8.8-beta", "o/r", {}))
        beta = derive_beta.beta_template(self.stable, "doorbell-map", self.source, current)
        self.assertEqual(sv.recorded(beta), "8.8.8-beta")
        self.assertEqual(beta, current)


if __name__ == "__main__":
    unittest.main()
