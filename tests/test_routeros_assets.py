from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RouterOSAssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.installer = (ROOT / "routeros" / "install.rsc").read_text()
        cls.uninstaller = (ROOT / "routeros" / "uninstall.rsc").read_text()

    def test_installer_has_pinned_objects_and_local_schedule(self) -> None:
        self.assertIn('name="auto-ir-ranges-sync"', self.installer)
        self.assertIn('name="auto-ir-ranges-daily"', self.installer)
        self.assertIn("start-time=03:00:00", self.installer)
        self.assertIn("disabled=yes", self.installer)
        self.assertLess(
            self.installer.index("/system script run auto-ir-ranges-sync"),
            self.installer.index("/system scheduler enable"),
        )

    def test_installer_validates_before_mutating(self) -> None:
        first_add = self.installer.index("/ip firewall address-list add")
        self.assertLess(self.installer.index("IPv4 SHA-512 mismatch"), first_add)
        self.assertLess(self.installer.index("IPv6 SHA-512 mismatch"), first_add)
        self.assertLess(self.installer.index("IPv6 count mismatch"), first_add)
        self.assertLess(self.installer.index("IPv6 shrink guard"), first_add)

    def test_routeros_missing_map_keys_use_nothing_type(self) -> None:
        self.assertIn('($desiredV4->$cidr)] != "nothing"', self.installer)
        self.assertIn('($desiredV6->$cidr)] != "nothing"', self.installer)
        self.assertIn('($desiredV4->$currentAddress)] = "nothing"', self.installer)
        self.assertIn('($desiredV6->$currentAddress)] = "nothing"', self.installer)

    def test_installer_does_not_create_firewall_rules(self) -> None:
        forbidden = (
            "/ip firewall filter add",
            "/ip firewall mangle add",
            "/ip firewall nat add",
            "/ipv6 firewall filter add",
            "/routing rule add",
            "/interface wireguard",
        )
        for command in forbidden:
            self.assertNotIn(command, self.installer)

    def test_uninstaller_retains_lists(self) -> None:
        self.assertNotIn("address-list remove", self.uninstaller)
        self.assertIn("updater removed; Iran address lists retained", self.uninstaller)


if __name__ == "__main__":
    unittest.main()
