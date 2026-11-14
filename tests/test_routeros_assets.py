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

    def test_sync_uses_linear_membership_scans(self) -> None:
        self.assertNotIn("find where list=$listV4 and address=$cidr", self.installer)
        self.assertNotIn("find where list=$listV6 and address=$cidr", self.installer)
        self.assertIn(":local presentV4", self.installer)
        self.assertIn(":local presentV6", self.installer)
        self.assertLess(
            self.installer.index("/ipv6 firewall address-list add"),
            self.installer.index("/ip firewall address-list remove $entryId"),
        )

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

    def sync_source(self) -> str:
        start = self.installer.index('/system script add name="auto-ir-ranges-sync"')
        end = self.installer.index('/system scheduler add name="auto-ir-ranges-daily"')
        return self.installer[start:end]

    def test_sync_keeps_no_recovery_state(self) -> None:
        source = self.sync_source()
        self.assertIn("policy=read,write,test ", source)
        self.assertNotIn("/file", source)
        self.assertNotIn(":delay", source)
        self.assertNotIn("auto-ir-ranges-state", source)
        self.assertNotIn("state.json", source)
        self.assertNotIn("recovery", source)
        self.assertNotIn("pendingGeneration", source)
        self.assertNotIn("dns_only_shrink", self.installer)
        first_add = source.index("/ip firewall address-list add")
        for line in (
            ':if ($staleV4 > $parsedV4) do={ :error ("auto-ir-ranges: IPv4 shrink guard; stale=" . $staleV4 . " feed=" . $parsedV4) }',
            ':if ($staleV6 > $parsedV6) do={ :error ("auto-ir-ranges: IPv6 shrink guard; stale=" . $staleV6 . " feed=" . $parsedV6) }',
        ):
            self.assertEqual(source.count(line), 1)
            self.assertLess(source.index(line), first_add)

    def test_version_gate_reads_digits_only(self) -> None:
        # A router that reports a bare "7.25" has neither a second dot nor a space.
        gate = self.installer[: self.installer.index("/system script job find")]
        self.assertIn(':while ([:pick $rosVersion $minorEnd ($minorEnd + 1)] ~ "[0-9]") do={ :set minorEnd ($minorEnd + 1) }', gate)
        self.assertNotIn("spaceAfterMinor", gate)
        self.assertNotIn('[:find $rosVersion " "', gate)

    def test_sync_normalises_ipv4_host_routes_once(self) -> None:
        source = self.sync_source()
        self.assertEqual(source.count(":if ($prefixLength = 32) do={ :set cidr $addressPart }"), 1)
        self.assertLess(
            source.index(":if ($prefixLength = 32) do={ :set cidr $addressPart }"),
            source.index('($desiredV4->$cidr)] != "nothing"'),
        )

    def test_installer_removes_old_leftovers_before_first_sync(self) -> None:
        cleanup = self.installer.index("Remove leftovers of older versions")
        self.assertLess(self.installer.index('/system scheduler add name="auto-ir-ranges-daily"'), cleanup)
        self.assertLess(cleanup, self.installer.index("/system script run auto-ir-ranges-sync"))
        for text in (self.installer, self.uninstaller):
            for name in ('"auto-ir-ranges/state.json"', '"auto-ir-ranges/state.json.tmp"', '"flash/auto-ir-ranges/state.json"', '"flash/auto-ir-ranges/state.json.tmp"', 'name="auto-ir-ranges-state"'):
                self.assertIn(name, text)

    def test_version_is_consistent(self) -> None:
        from auto_ir_ranges import __version__

        self.assertEqual(__version__, "2.1.0")
        self.assertIn(f'version = "{__version__}"', (ROOT / "pyproject.toml").read_text())
        self.assertEqual(self.installer.count(f"version={__version__}"), 2)
        self.assertIn(f"# mikrotik-auto-ir-ranges v{__version__} installer", self.installer)
        self.assertIn(f"auto-ir-ranges: v{__version__} installed", self.installer)
        self.assertIn(f"# mikrotik-auto-ir-ranges v{__version__} uninstaller", self.uninstaller)
        self.assertIn(f"/v{__version__}/routeros/install.rsc", (ROOT / "README.md").read_text())

    def test_uninstaller_retains_lists(self) -> None:
        self.assertNotIn("address-list remove", self.uninstaller)
        self.assertIn("updater removed; Iran address lists retained", self.uninstaller)


if __name__ == "__main__":
    unittest.main()
