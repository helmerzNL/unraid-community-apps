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
        self.assertEqual(names, {"discvault", "discvault-beta", "doorbell-map", "doorbell-map-beta", "mealieshopper", "mealieshopper-beta"})

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

    def test_missing_english_or_dutch(self):
        self.edit("mealieshopper.xml", "Nederlands:", "Dutch:")
        self.assertTrue(any("Nederlands" in p for p in self.problems()))

    def test_empty_profile(self):
        path = self.tmp / "ca_profile.xml"
        path.write_text('<?xml version="1.0"?><Maintainer><Profile> </Profile></Maintainer>', encoding="utf-8")
        self.assertTrue(any("Profile" in p for p in self.problems()))


if __name__ == "__main__":
    unittest.main()
