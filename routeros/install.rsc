# mikrotik-auto-ir-ranges v1.0.1 installer
# RouterOS 7.20+; data updates daily at 03:00 router-local time.

:local rosVersion [/system resource get version]
:local firstDot [:find $rosVersion "."]
:if ([:typeof $firstDot] = "nil") do={ :error ("auto-ir-ranges: cannot parse RouterOS version " . $rosVersion) }
:local majorVersion [:tonum [:pick $rosVersion 0 $firstDot]]
:if ([:typeof $majorVersion] != "num") do={ :error "auto-ir-ranges: invalid RouterOS major version" }
:if ($majorVersion < 7) do={ :error "auto-ir-ranges: RouterOS 7.20 or newer is required" }
:if ($majorVersion = 7) do={
    :local minorEnd [:find $rosVersion "." ($firstDot + 1)]
    :if ([:typeof $minorEnd] = "nil") do={
        :local spaceAfterMinor [:find $rosVersion " " ($firstDot + 1)]
        :if ([:typeof $spaceAfterMinor] = "nil") do={ :error "auto-ir-ranges: cannot parse RouterOS minor version" }
        :local minorVersion [:tonum [:pick $rosVersion ($firstDot + 1) $spaceAfterMinor]]
        :if ([:typeof $minorVersion] != "num") do={ :error "auto-ir-ranges: invalid RouterOS minor version" }
        :if ($minorVersion < 20) do={ :error "auto-ir-ranges: RouterOS 7.20 or newer is required" }
    } else={
        :local minorVersion [:tonum [:pick $rosVersion ($firstDot + 1) $minorEnd]]
        :if ([:typeof $minorVersion] != "num") do={ :error "auto-ir-ranges: invalid RouterOS minor version" }
        :if ($minorVersion < 20) do={ :error "auto-ir-ranges: RouterOS 7.20 or newer is required" }
    }
}

:if ([:len [/system scheduler find where name="auto-ir-ranges-daily"]] > 0) do={
    /system scheduler disable [/system scheduler find where name="auto-ir-ranges-daily"]
}
:if ([:len [/system script find where name="auto-ir-ranges-sync"]] > 0) do={
    /system script remove [/system script find where name="auto-ir-ranges-sync"]
}

/system script add name="auto-ir-ranges-sync" policy=read,write,test comment="managed:mikrotik-auto-ir-ranges version=1.0.1" source={
    :local manifestUrl "https://raw.githubusercontent.com/parhamfa/mikrotik-auto-ir-ranges/data/manifest.json"
    :local ipv4Url "https://raw.githubusercontent.com/parhamfa/mikrotik-auto-ir-ranges/data/ir-ipv4.zone"
    :local ipv6Url "https://raw.githubusercontent.com/parhamfa/mikrotik-auto-ir-ranges/data/ir-ipv6.zone"
    :local listV4 "Iran_IPV4"
    :local listV6 "Iran_IPV6"
    :local managedComment "managed:mikrotik-auto-ir-ranges"
    :local maxFeedBytes 61440

    # Fetch all inputs before touching either address list.
    :local manifestResponse
    :onerror fetchError in={
        :set manifestResponse [/tool fetch url=$manifestUrl check-certificate=yes output=user as-value]
    } do={
        :log error ("auto-ir-ranges: manifest fetch failed: " . $fetchError)
        :error $fetchError
    }
    :if (($manifestResponse->"status") != "finished") do={ :error "auto-ir-ranges: manifest fetch did not finish" }
    :local manifestText ($manifestResponse->"data")
    :local manifest [:deserialize from=json value=$manifestText options=json.no-string-conversion]
    :if ([:tonum ($manifest->"schema")] != 1) do={ :error "auto-ir-ranges: unsupported manifest schema" }
    :if (($manifest->"country") != "IR") do={ :error "auto-ir-ranges: manifest country is not IR" }
    :local generatedAt [:tostr ($manifest->"generated_at")]
    :if ([:len $generatedAt] = 0) do={ :error "auto-ir-ranges: missing generation timestamp" }

    :local metaV4 ($manifest->"ipv4")
    :local metaV6 ($manifest->"ipv6")
    :if (($metaV4->"file") != "ir-ipv4.zone") do={ :error "auto-ir-ranges: unexpected IPv4 filename" }
    :if (($metaV6->"file") != "ir-ipv6.zone") do={ :error "auto-ir-ranges: unexpected IPv6 filename" }
    :local expectedV4 [:tonum ($metaV4->"count")]
    :local expectedV6 [:tonum ($metaV6->"count")]
    :local expectedV4Bytes [:tonum ($metaV4->"bytes")]
    :local expectedV6Bytes [:tonum ($metaV6->"bytes")]
    :local expectedV4Hash [:tostr ($metaV4->"sha512")]
    :local expectedV6Hash [:tostr ($metaV6->"sha512")]
    :if ([:typeof $expectedV4] != "num") do={ :error "auto-ir-ranges: invalid IPv4 count" }
    :if ([:typeof $expectedV6] != "num") do={ :error "auto-ir-ranges: invalid IPv6 count" }
    :if (($expectedV4 < 1000) || ($expectedV4 > 5000)) do={ :error ("auto-ir-ranges: implausible IPv4 count " . $expectedV4) }
    :if (($expectedV6 < 300) || ($expectedV6 > 2000)) do={ :error ("auto-ir-ranges: implausible IPv6 count " . $expectedV6) }
    :if (($expectedV4Bytes < 1) || ($expectedV4Bytes > $maxFeedBytes)) do={ :error "auto-ir-ranges: invalid IPv4 byte size" }
    :if (($expectedV6Bytes < 1) || ($expectedV6Bytes > $maxFeedBytes)) do={ :error "auto-ir-ranges: invalid IPv6 byte size" }
    :if ([:len $expectedV4Hash] != 128) do={ :error "auto-ir-ranges: invalid IPv4 SHA-512" }
    :if ([:len $expectedV6Hash] != 128) do={ :error "auto-ir-ranges: invalid IPv6 SHA-512" }

    :local ipv4Response
    :onerror fetchError in={
        :set ipv4Response [/tool fetch url=$ipv4Url check-certificate=yes output=user as-value]
    } do={
        :log error ("auto-ir-ranges: IPv4 fetch failed: " . $fetchError)
        :error $fetchError
    }
    :if (($ipv4Response->"status") != "finished") do={ :error "auto-ir-ranges: IPv4 fetch did not finish" }
    :local ipv4Data ($ipv4Response->"data")

    :local ipv6Response
    :onerror fetchError in={
        :set ipv6Response [/tool fetch url=$ipv6Url check-certificate=yes output=user as-value]
    } do={
        :log error ("auto-ir-ranges: IPv6 fetch failed: " . $fetchError)
        :error $fetchError
    }
    :if (($ipv6Response->"status") != "finished") do={ :error "auto-ir-ranges: IPv6 fetch did not finish" }
    :local ipv6Data ($ipv6Response->"data")

    # Verify transport content against the manifest.
    :if ([:len $ipv4Data] != $expectedV4Bytes) do={ :error "auto-ir-ranges: IPv4 byte-size mismatch" }
    :if ([:len $ipv6Data] != $expectedV6Bytes) do={ :error "auto-ir-ranges: IPv6 byte-size mismatch" }
    :local actualV4Hash [:convert $ipv4Data transform=sha512 to=hex]
    :local actualV6Hash [:convert $ipv6Data transform=sha512 to=hex]
    :if ($actualV4Hash != $expectedV4Hash) do={ :error "auto-ir-ranges: IPv4 SHA-512 mismatch" }
    :if ($actualV6Hash != $expectedV6Hash) do={ :error "auto-ir-ranges: IPv6 SHA-512 mismatch" }

    # Parse and validate complete IPv4 data into an associative membership map.
    :local desiredV4 [:toarray ""]
    :local parsedV4 0
    :local v4Offset 0
    :local v4Length [:len $ipv4Data]
    :if ([:pick $ipv4Data ($v4Length - 1) $v4Length] != "\n") do={ :error "auto-ir-ranges: IPv4 feed lacks final newline" }
    :while ($v4Offset < $v4Length) do={
        :local newline [:find $ipv4Data "\n" $v4Offset]
        :if ([:typeof $newline] = "nil") do={ :set newline $v4Length }
        :local cidr [:pick $ipv4Data $v4Offset $newline]
        :set v4Offset ($newline + 1)
        :if ([:len $cidr] = 0) do={ :error "auto-ir-ranges: blank IPv4 feed row" }
        :local slash [:find $cidr "/"]
        :if ([:typeof $slash] = "nil") do={ :error ("auto-ir-ranges: malformed IPv4 CIDR " . $cidr) }
        :if ([:typeof [:find $cidr "/" ($slash + 1)]] != "nil") do={ :error ("auto-ir-ranges: malformed IPv4 CIDR " . $cidr) }
        :if ([:typeof [:find $cidr ":"]] != "nil") do={ :error ("auto-ir-ranges: wrong family in IPv4 feed " . $cidr) }
        :local addressPart [:pick $cidr 0 $slash]
        :local prefixLength [:tonum [:pick $cidr ($slash + 1) [:len $cidr]]]
        :if ([:typeof [:toip $addressPart]] != "ip") do={ :error ("auto-ir-ranges: invalid IPv4 address " . $cidr) }
        :if ([:typeof $prefixLength] != "num") do={ :error ("auto-ir-ranges: invalid IPv4 prefix " . $cidr) }
        :if (($prefixLength < 0) || ($prefixLength > 32)) do={ :error ("auto-ir-ranges: invalid IPv4 prefix " . $cidr) }
        :if ([:typeof ($desiredV4->$cidr)] != "nothing") do={ :error ("auto-ir-ranges: duplicate IPv4 CIDR " . $cidr) }
        :set ($desiredV4->$cidr) true
        :set parsedV4 ($parsedV4 + 1)
    }
    :if ($parsedV4 != $expectedV4) do={ :error ("auto-ir-ranges: IPv4 count mismatch " . $parsedV4 . "/" . $expectedV4) }

    # Parse and validate complete IPv6 data before any mutation.
    :local desiredV6 [:toarray ""]
    :local parsedV6 0
    :local v6Offset 0
    :local v6Length [:len $ipv6Data]
    :if ([:pick $ipv6Data ($v6Length - 1) $v6Length] != "\n") do={ :error "auto-ir-ranges: IPv6 feed lacks final newline" }
    :while ($v6Offset < $v6Length) do={
        :local newline [:find $ipv6Data "\n" $v6Offset]
        :if ([:typeof $newline] = "nil") do={ :set newline $v6Length }
        :local cidr [:pick $ipv6Data $v6Offset $newline]
        :set v6Offset ($newline + 1)
        :if ([:len $cidr] = 0) do={ :error "auto-ir-ranges: blank IPv6 feed row" }
        :local slash [:find $cidr "/"]
        :if ([:typeof $slash] = "nil") do={ :error ("auto-ir-ranges: malformed IPv6 CIDR " . $cidr) }
        :if ([:typeof [:find $cidr "/" ($slash + 1)]] != "nil") do={ :error ("auto-ir-ranges: malformed IPv6 CIDR " . $cidr) }
        :if ([:typeof [:find $cidr ":"]] = "nil") do={ :error ("auto-ir-ranges: wrong family in IPv6 feed " . $cidr) }
        :local addressPart [:pick $cidr 0 $slash]
        :local prefixLength [:tonum [:pick $cidr ($slash + 1) [:len $cidr]]]
        :if ([:typeof [:toip6 $addressPart]] != "ip6") do={ :error ("auto-ir-ranges: invalid IPv6 address " . $cidr) }
        :if ([:typeof $prefixLength] != "num") do={ :error ("auto-ir-ranges: invalid IPv6 prefix " . $cidr) }
        :if (($prefixLength < 0) || ($prefixLength > 128)) do={ :error ("auto-ir-ranges: invalid IPv6 prefix " . $cidr) }
        :if ([:typeof ($desiredV6->$cidr)] != "nothing") do={ :error ("auto-ir-ranges: duplicate IPv6 CIDR " . $cidr) }
        :set ($desiredV6->$cidr) true
        :set parsedV6 ($parsedV6 + 1)
    }
    :if ($parsedV6 != $expectedV6) do={ :error ("auto-ir-ranges: IPv6 count mismatch " . $parsedV6 . "/" . $expectedV6) }

    # Compare with the currently installed generation before changing anything.
    :local currentV4 [:len [/ip firewall address-list find where list=$listV4]]
    :local currentV6 [:len [/ipv6 firewall address-list find where list=$listV6]]
    :if ($currentV4 > 0) do={
        :if (($parsedV4 * 2) < $currentV4) do={ :error ("auto-ir-ranges: IPv4 shrink guard " . $currentV4 . "->" . $parsedV4) }
    }
    :if ($currentV6 > 0) do={
        :if (($parsedV6 * 2) < $currentV6) do={ :error ("auto-ir-ranges: IPv6 shrink guard " . $currentV6 . "->" . $parsedV6) }
    }

    :local addedV4 0
    :local adoptedV4 0
    :local removedV4 0
    :local duplicateV4 0
    :foreach cidr,wanted in=$desiredV4 do={
        :local matching [/ip firewall address-list find where list=$listV4 and address=$cidr]
        :if ([:len $matching] = 0) do={
            /ip firewall address-list add list=$listV4 address=$cidr comment=$managedComment
            :set addedV4 ($addedV4 + 1)
        } else={
            :local keeperChosen false
            :foreach entryId in=$matching do={
                :if ($keeperChosen = false) do={
                    :set keeperChosen true
                    :local needsAdoption false
                    :if ([/ip firewall address-list get $entryId comment] != $managedComment) do={ :set needsAdoption true }
                    :if ([/ip firewall address-list get $entryId disabled] = true) do={ :set needsAdoption true }
                    :if ($needsAdoption = true) do={
                        /ip firewall address-list set $entryId comment=$managedComment disabled=no
                        :set adoptedV4 ($adoptedV4 + 1)
                    }
                } else={
                    /ip firewall address-list remove $entryId
                    :set duplicateV4 ($duplicateV4 + 1)
                }
            }
        }
    }
    :foreach entryId in=[/ip firewall address-list find where list=$listV4] do={
        :local currentAddress [:tostr [/ip firewall address-list get $entryId address]]
        :if ([:typeof ($desiredV4->$currentAddress)] = "nothing") do={
            /ip firewall address-list remove $entryId
            :set removedV4 ($removedV4 + 1)
        }
    }

    :local addedV6 0
    :local adoptedV6 0
    :local removedV6 0
    :local duplicateV6 0
    :foreach cidr,wanted in=$desiredV6 do={
        :local matching [/ipv6 firewall address-list find where list=$listV6 and address=$cidr]
        :if ([:len $matching] = 0) do={
            /ipv6 firewall address-list add list=$listV6 address=$cidr comment=$managedComment
            :set addedV6 ($addedV6 + 1)
        } else={
            :local keeperChosen false
            :foreach entryId in=$matching do={
                :if ($keeperChosen = false) do={
                    :set keeperChosen true
                    :local needsAdoption false
                    :if ([/ipv6 firewall address-list get $entryId comment] != $managedComment) do={ :set needsAdoption true }
                    :if ([/ipv6 firewall address-list get $entryId disabled] = true) do={ :set needsAdoption true }
                    :if ($needsAdoption = true) do={
                        /ipv6 firewall address-list set $entryId comment=$managedComment disabled=no
                        :set adoptedV6 ($adoptedV6 + 1)
                    }
                } else={
                    /ipv6 firewall address-list remove $entryId
                    :set duplicateV6 ($duplicateV6 + 1)
                }
            }
        }
    }
    :foreach entryId in=[/ipv6 firewall address-list find where list=$listV6] do={
        :local currentAddress [:tostr [/ipv6 firewall address-list get $entryId address]]
        :if ([:typeof ($desiredV6->$currentAddress)] = "nothing") do={
            /ipv6 firewall address-list remove $entryId
            :set removedV6 ($removedV6 + 1)
        }
    }

    :local finalV4 [:len [/ip firewall address-list find where list=$listV4]]
    :local finalV6 [:len [/ipv6 firewall address-list find where list=$listV6]]
    :local ownedV4 [:len [/ip firewall address-list find where list=$listV4 and comment=$managedComment and disabled=no]]
    :local ownedV6 [:len [/ipv6 firewall address-list find where list=$listV6 and comment=$managedComment and disabled=no]]
    :if (($finalV4 != $parsedV4) || ($ownedV4 != $parsedV4)) do={ :error ("auto-ir-ranges: final IPv4 mismatch " . $finalV4 . "/" . $ownedV4 . "/" . $parsedV4) }
    :if (($finalV6 != $parsedV6) || ($ownedV6 != $parsedV6)) do={ :error ("auto-ir-ranges: final IPv6 mismatch " . $finalV6 . "/" . $ownedV6 . "/" . $parsedV6) }

    :local totalChanges ($addedV4 + $adoptedV4 + $removedV4 + $duplicateV4 + $addedV6 + $adoptedV6 + $removedV6 + $duplicateV6)
    :if ($totalChanges = 0) do={
        :log info ("auto-ir-ranges: unchanged generated=" . $generatedAt . " ipv4=" . $finalV4 . " ipv6=" . $finalV6)
    } else={
        :log info ("auto-ir-ranges: synced generated=" . $generatedAt . " ipv4=" . $finalV4 . " ipv6=" . $finalV6 . " added=" . ($addedV4 + $addedV6) . " adopted=" . ($adoptedV4 + $adoptedV6) . " stale=" . ($removedV4 + $removedV6) . " duplicates=" . ($duplicateV4 + $duplicateV6))
    }
}

:if ([:len [/system scheduler find where name="auto-ir-ranges-daily"]] > 0) do={
    /system scheduler remove [/system scheduler find where name="auto-ir-ranges-daily"]
}
/system scheduler add name="auto-ir-ranges-daily" disabled=yes start-time=03:00:00 interval=1d on-event="auto-ir-ranges-sync" policy=read,write,test comment="managed:mikrotik-auto-ir-ranges version=1.0.1"

:onerror syncError in={
    /system script run auto-ir-ranges-sync
} do={
    :log error ("auto-ir-ranges: initial sync failed; scheduler remains disabled: " . $syncError)
    :error $syncError
}
/system scheduler enable [/system scheduler find where name="auto-ir-ranges-daily"]
:log info "auto-ir-ranges: v1.0.1 installed; daily schedule enabled at 03:00 local time"
