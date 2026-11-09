import shutil
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.dont_write_bytecode = True

import check_templates  # noqa: E402
import derive_beta  # noqa: E402


class RepositoryTest(unittest.TestCase):
    def test_repository_is_in_order(self):
        self.assertEqual(check_templates.check_repository(), [])

    def test_every_app_has_a_stable_and_a_beta_template(self):
        names = {path.stem for path in (ROOT / "templates").glob("*.xml")}
        self.assertEqual(names, {"discvault", "discvault-beta", "doorbell-map", "doorbell-map-beta", "mealieshopper", "mealieshopper-beta", "nas-storage-analyzer", "nas-storage-analyzer-beta", "printpricing", "printpricing-beta"})

    def test_beta_matches_stable(self):
        self.assertEqual(derive_beta.differences(), [])


class BetaTemplateTest(unittest.TestCase):
    def setUp(self):
        self.stable = ET.parse(ROOT / "templates" / "doorbell-map.xml").getroot()
        self.beta = ET.parse(ROOT / "templates" / "doorbell-map-beta.xml").getroot()

    def config(self, root, type_):
        return [c for c in root.findall("Config") if c.get("Type") == type_]

    def test_name_tag_and_label(self):
        self.assertEqual(self.beta.find("Name").text, "Doorbell Map (beta)")
        self.assertTrue(self.beta.find("Repository").text.endswith(":beta"))
        self.assertEqual(self.beta.find("Beta").text, "true")
        self.assertIsNone(self.stable.find("Beta"))

    def test_host_port_is_one_higher_in_default_and_value(self):
        stable, beta = self.config(self.stable, "Port")[0], self.config(self.beta, "Port")[0]
        self.assertEqual((stable.get("Default"), stable.text), ("9888", "9888"))
        self.assertEqual((beta.get("Default"), beta.text), ("9889", "9889"))
        self.assertEqual(beta.get("Target"), stable.get("Target"))          # the container port does not change

    def test_own_app_data_folder(self):
        self.assertEqual(self.config(self.beta, "Path")[0].get("Default"), "/mnt/user/appdata/doorbell-map-beta")

    def test_hidden_variable_for_the_beta_label(self):
        label = [c for c in self.beta.findall("Config") if c.get("Target") == "DOORBELL_MAP_TAG"]
        self.assertEqual([(c.get("Display"), c.text) for c in label], [("always-hide", "beta")])


class DiscVaultStackTest(unittest.TestCase):
    """The DiscVault launcher manages a stack; stable and beta must be able to run next to each other."""

    def setUp(self):
        self.stable = ET.parse(ROOT / "templates" / "discvault.xml").getroot()
        self.beta = ET.parse(ROOT / "templates" / "discvault-beta.xml").getroot()

    def by_target(self, root):
        return {c.get("Target"): c for c in root.findall("Config")}

    def test_launcher_images(self):
        self.assertEqual(self.stable.find("Repository").text, "ghcr.io/helmerznl/discvault-launcher:latest")
        self.assertEqual(self.beta.find("Repository").text, "ghcr.io/helmerznl/discvault-launcher:beta")

    def test_channel_dropdown_starts_on_the_right_value(self):
        stable, beta = self.by_target(self.stable)["DISCVAULT_CHANNEL"], self.by_target(self.beta)["DISCVAULT_CHANNEL"]
        self.assertEqual((stable.get("Default").split("|")[0], stable.text), ("latest", "latest"))
        self.assertEqual((beta.get("Default").split("|")[0], beta.text), ("beta", "beta"))
        self.assertEqual(sorted(beta.get("Default").split("|")), sorted(stable.get("Default").split("|")))

    def test_every_appdata_folder_of_the_beta_is_its_own(self):
        stable, beta = self.by_target(self.stable), self.by_target(self.beta)
        for target in ("/data", "/config", "DISCVAULT_DATA_DIR_HOST", "DISCVAULT_POSTGRES_DATA_DIR_HOST"):
            self.assertNotEqual(stable[target].text, beta[target].text, target)
            self.assertTrue(beta[target].text.startswith("/mnt/user/appdata/discvault-beta"), target)
        self.assertEqual(beta["/data"].text, beta["DISCVAULT_DATA_DIR_HOST"].text)

    def test_own_project_network_and_port(self):
        stable, beta = self.by_target(self.stable), self.by_target(self.beta)
        self.assertEqual(beta["DISCVAULT_PROJECT_NAME"].text, "discvault_beta_stack")
        self.assertEqual(beta["DISCVAULT_NETWORK"].text, "discvault-beta-stack")
        self.assertEqual((stable["80"].text, beta["80"].text), ("6080", "6081"))

    def test_docker_socket_is_the_same_and_visible(self):
        for root in (self.stable, self.beta):
            socket = self.by_target(root)["/var/run/docker.sock"]
            self.assertEqual((socket.get("Display"), socket.get("Required")), ("always", "true"))

    def test_launcher_variables_only(self):
        """The launcher does not pass the DISCVAULT_OIDC_* variables on, so the template must not offer them."""
        self.assertFalse([t for t in self.by_target(self.stable) if "OIDC" in t])


class PrintPricingTest(unittest.TestCase):
    """3D Print Pricing keeps a SQLite file in its app data folder: that must be a local disk, and the secrets must be asked for."""

    def setUp(self):
        self.stable = ET.parse(ROOT / "templates" / "printpricing.xml").getroot()
        self.beta = ET.parse(ROOT / "templates" / "printpricing-beta.xml").getroot()

    def by_target(self, root):
        return {c.get("Target"): c for c in root.findall("Config")}

    def test_database_folder_is_not_on_the_fuse_share(self):
        for root in (self.stable, self.beta):
            folder = self.by_target(root)["/data"].get("Default")
            self.assertTrue(folder.startswith("/mnt/cache/appdata/"), folder)

    def test_beta_has_its_own_folder_port_and_label(self):
        stable, beta = self.by_target(self.stable), self.by_target(self.beta)
        self.assertEqual(beta["/data"].text, "/mnt/cache/appdata/printpricing-beta")
        self.assertEqual((stable["6080"].text, beta["6080"].text), ("6280", "6281"))        # the container port stays 6080
        self.assertEqual((beta["PRINTPRICING_TAG"].text, beta["PRINTPRICING_TAG"].get("Display")), ("beta", "always-hide"))
        self.assertNotIn("PRINTPRICING_TAG", stable)

    def test_secrets_are_required_masked_and_have_no_default(self):
        for root in (self.stable, self.beta):
            for target in ("SETUP_CODE", "SESSION_SECRET"):
                entry = self.by_target(root)[target]
                self.assertEqual((entry.get("Required"), entry.get("Mask"), entry.get("Default")), ("true", "true", ""), target)

    def test_domain_is_optional_and_empty(self):
        for target in ("RP_ID", "RP_ORIGINS"):
            entry = self.by_target(self.stable)[target]
            self.assertEqual((entry.get("Required"), entry.get("Default")), ("false", ""), target)

    def test_no_postgresql_settings(self):
        self.assertFalse([t for t in self.by_target(self.stable) if "POSTGRES" in t or "DATABASE_URL" in t])


class DoorbellMapTest(unittest.TestCase):
    """Since version 1.0.35 Doorbell Map follows the address it is opened on, so Origin is optional; the two secrets are not."""

    def test_origin_is_optional_and_the_secrets_are_required(self):
        for name in ("doorbell-map.xml", "doorbell-map-beta.xml"):
            by_target = {c.get("Target"): c for c in ET.parse(ROOT / "templates" / name).getroot().findall("Config")}
            self.assertEqual((by_target["ORIGIN"].get("Required"), by_target["ORIGIN"].get("Default")), ("false", ""), name)
            for target in ("SETUP_TOKEN", "SESSION_SECRET"):
                entry = by_target[target]
                self.assertEqual((entry.get("Required"), entry.get("Mask")), ("true", "true"), (name, target))


class MealieShopperTest(unittest.TestCase):
    """Since 3.14.0 MealieShopper reads ANTHROPIC_BASE_URL and OPENAI_BASE_URL (mealieshopper/ai_review.py): another address for the AI review,
    for example a LiteLLM proxy. Empty means the default address, so they are optional, advanced and empty."""

    def test_the_ai_addresses_are_optional_advanced_and_empty(self):
        for name in ("mealieshopper.xml", "mealieshopper-beta.xml"):
            by_target = {c.get("Target"): c for c in ET.parse(ROOT / "templates" / name).getroot().findall("Config")}
            for target in ("ANTHROPIC_BASE_URL", "OPENAI_BASE_URL"):
                entry = by_target[target]
                self.assertEqual((entry.get("Type"), entry.get("Display"), entry.get("Required"), entry.get("Default"), entry.text),
                                 ("Variable", "advanced", "false", "", None), (name, target))


class NasStorageAnalyzerTest(unittest.TestCase):
    """NAS Storage Analyzer runs its all-in-one image: the database on a local disk, the shares read-only, the encryption key required."""

    def setUp(self):
        self.stable = ET.parse(ROOT / "templates" / "nas-storage-analyzer.xml").getroot()
        self.beta = ET.parse(ROOT / "templates" / "nas-storage-analyzer-beta.xml").getroot()

    def by_target(self, root):
        return {c.get("Target"): c for c in root.findall("Config")}

    def test_all_in_one_images(self):
        self.assertEqual(self.stable.find("Repository").text, "ghcr.io/helmerznl/nas-storage-analyzer-aio:latest")
        self.assertEqual(self.beta.find("Repository").text, "ghcr.io/helmerznl/nas-storage-analyzer-aio:beta")

    def test_the_shares_are_read_only_at_the_same_path_the_app_may_scan(self):
        for root in (self.stable, self.beta):
            shares, entries = self.by_target(root)["/mnt/user"], self.by_target(root)
            self.assertEqual((shares.get("Default"), shares.get("Mode")), ("/mnt/user", "ro"))
            self.assertEqual(entries["NASSA_SHARES_ROOT"].text, "/mnt/user", "the app may only scan below the folder it gets")

    def test_database_on_a_local_disk_and_its_own_for_the_beta(self):
        stable, beta = self.by_target(self.stable)["/data"], self.by_target(self.beta)["/data"]
        self.assertEqual(stable.text, "/mnt/cache/appdata/nas-storage-analyzer")
        self.assertEqual(beta.text, "/mnt/cache/appdata/nas-storage-analyzer-beta")

    def test_the_encryption_key_is_required_masked_and_has_no_default(self):
        for root in (self.stable, self.beta):
            key = self.by_target(root)["NASSA_ENCRYPTION_KEY"]
            self.assertEqual((key.get("Required"), key.get("Mask"), key.get("Default")), ("true", "true", ""))

    def test_port_and_shared_memory(self):
        stable, beta = self.by_target(self.stable)["8080"], self.by_target(self.beta)["8080"]
        self.assertEqual((stable.text, beta.text), ("6480", "6481"))
        for root in (self.stable, self.beta):
            self.assertIn("--shm-size=256m", root.find("ExtraParams").text)
            self.assertEqual(root.find("Privileged").text, "false", "the shares are local on Unraid: no rights to mount")


class DefectsAreCaughtTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        for item in ROOT.iterdir():
            if item.name not in (".git", "__pycache__"):
                (shutil.copytree if item.is_dir() else shutil.copy)(item, self.tmp / item.name)

    def edit(self, name, old, new):
        path = self.tmp / "templates" / name
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text)
        path.write_text(text.replace(old, new), encoding="utf-8")

    def problems(self):
        return check_templates.check_repository(self.tmp)

    def test_uppercase_image(self):
        self.edit("mealieshopper.xml", "ghcr.io/helmerznl/mealieshopper:latest", "ghcr.io/helmerzNL/MealieShopper:latest")
        self.assertTrue(any("Repository" in p for p in self.problems()))

    def test_unmasked_secret(self):
        self.edit("doorbell-map.xml", 'Type="Variable" Display="always" Required="true" Mask="true">', 'Type="Variable" Display="always" Required="true" Mask="false">')
        self.assertTrue(any("Mask" in p for p in self.problems()))

    def test_html_in_a_description(self):
        self.edit("doorbell-map.xml", "for example https://map.example.com.", "for example &lt;your address&gt;.")
        self.assertTrue(any("HTML" in p for p in self.problems()))

    def test_duplicate_host_port(self):
        self.edit("doorbell-map.xml", 'Default="9888" Mode="tcp"', 'Default="5959" Mode="tcp"')
        self.assertTrue(any("5959" in p for p in self.problems()))

    def test_missing_support(self):
        self.edit("mealieshopper.xml", "<Support>https://github.com/helmerzNL/MealieShopper/issues</Support>", "<Support></Support>")
        self.assertTrue(any("Support" in p for p in self.problems()))

    def test_wrong_template_url(self):
        self.edit("mealieshopper.xml", "/templates/mealieshopper.xml", "/templates/other.xml")
        self.assertTrue(any("TemplateURL" in p for p in self.problems()))

    def test_beta_that_drifted(self):
        self.edit("doorbell-map-beta.xml", "<Date>2026-10-28</Date>", "<Date>2026-01-01</Date>")
        self.assertTrue(derive_beta.differences(self.tmp))

    def test_host_data_folder_must_match(self):
        self.edit("discvault.xml", 'Default="/mnt/user/appdata/discvault" Mode="" Description="The same folder', 'Default="/mnt/user/appdata/other" Mode="" Description="The same folder')
        self.assertTrue(any("DISCVAULT_DATA_DIR_HOST" in p for p in self.problems()))

    def test_two_stacks_cannot_share_a_project_name(self):
        self.edit("discvault-beta.xml", ">discvault_beta_stack<", ">discvault_stack<")
        self.assertTrue(any("DISCVAULT_PROJECT_NAME" in p for p in self.problems()))

    def test_two_templates_cannot_share_a_folder(self):
        self.edit("doorbell-map-beta.xml", "/mnt/user/appdata/doorbell-map-beta", "/mnt/user/appdata/doorbell-map")
        self.assertTrue(any("also used by" in p for p in self.problems()))

    def test_unmasked_encryption_key(self):
        self.edit("discvault.xml", 'Type="Variable" Display="advanced" Required="false" Mask="true"></Config>\n  <Config Name="Compose project', 'Type="Variable" Display="advanced" Required="false" Mask="false"></Config>\n  <Config Name="Compose project')
        self.assertTrue(any("Mask" in p for p in self.problems()))

    def test_a_switch_named_password_is_not_a_secret(self):
        self.assertEqual([p for p in self.problems() if "SYNC_PASSWORD" in p], [])

    def test_sqlite_folder_on_the_user_share_is_refused(self):
        self.edit("printpricing.xml", "/mnt/cache/appdata/printpricing", "/mnt/user/appdata/printpricing")
        self.assertTrue(any("FUSE" in p for p in self.problems()))

    def test_unmasked_setup_code(self):
        self.edit("printpricing.xml", 'Type="Variable" Display="always" Required="true" Mask="true"></Config>\n  <Config Name="Session secret"', 'Type="Variable" Display="always" Required="true" Mask="false"></Config>\n  <Config Name="Session secret"')
        self.assertTrue(any("SETUP_CODE" in p for p in self.problems()))

    def test_two_apps_cannot_share_a_host_port(self):
        self.edit("printpricing.xml", 'Default="6280" Mode="tcp"', 'Default="6080" Mode="tcp"')
        self.assertTrue(any("6080" in p for p in self.problems()))

    def test_missing_english_or_dutch(self):
        self.edit("mealieshopper.xml", "Nederlands:", "Dutch:")
        self.assertTrue(any("Nederlands" in p for p in self.problems()))

    def test_a_writable_folder_outside_appdata_is_refused(self):
        self.edit("nas-storage-analyzer.xml", 'Default="/mnt/user" Mode="ro"', 'Default="/mnt/user" Mode="rw"')
        self.assertTrue(any("writable Path" in p for p in self.problems()))

    def test_a_read_only_folder_must_be_below_mnt(self):
        self.edit("nas-storage-analyzer.xml", 'Target="/mnt/user" Default="/mnt/user" Mode="ro"', 'Target="/mnt/user" Default="/" Mode="ro"')
        self.assertTrue(any("read-only Path" in p for p in self.problems()))

    def test_empty_profile(self):
        path = self.tmp / "ca_profile.xml"
        path.write_text('<?xml version="1.0"?><Maintainer><Profile> </Profile></Maintainer>', encoding="utf-8")
        self.assertTrue(any("Profile" in p for p in self.problems()))


if __name__ == "__main__":
    unittest.main()
