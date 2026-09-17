# mikrotik-auto-ir-ranges v2.0.0 installer
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

# Persistent journal survives reboots and is retained by reinstalls.
:if ([:len [/system script find where name="auto-ir-ranges-state"]] = 0) do={
    /system script add name="auto-ir-ranges-state" policy=read source="" comment=""
}
/system script add name="auto-ir-ranges-sync" policy=read,write,test comment="managed:mikrotik-auto-ir-ranges version=2.0.0" source={
    :if ([:len [/system script job find where script="auto-ir-ranges-sync"]] > 1) do={ :error "auto-ir-ranges: another sync is running" }
    :local baseUrl "https://raw.githubusercontent.com/parhamfa/mikrotik-auto-ir-ranges/data/"
    :local manifestUrl ($baseUrl . "manifest-v2.json")
    :local listV4 "Iran_IPV4"
    :local listV6 "Iran_IPV6"
    :local managedComment "managed:mikrotik-auto-ir-ranges"
    :local maxPageBytes 49152
    :local currentV4 [:len [/ip firewall address-list find where list=$listV4]]
    :local currentV6 [:len [/ipv6 firewall address-list find where list=$listV6]]
    :local baselineV4 $currentV4
    :local baselineV6 $currentV6
    :local journalText [/system script get [find where name="auto-ir-ranges-state"] comment]
    :local pendingGeneration ""
    :local journal
    :if ([:len $journalText] > 0) do={
        :set journal [:deserialize from=json value=$journalText options=json.no-string-conversion]
        :set pendingGeneration ($journal->"generation")
        :if (([:len $pendingGeneration] != 64) || ($pendingGeneration ~ "[^0-9a-f]")) do={ :error "auto-ir-ranges: invalid recovery journal" }
        :set manifestUrl ($baseUrl . "generations/" . $pendingGeneration . "/manifest.json")
        :set baselineV4 ($journal->"previous4")
        :set baselineV6 ($journal->"previous6")
        :log warning ("auto-ir-ranges: resuming interrupted generation " . $pendingGeneration)
    }
    :local manifestResponse [/tool fetch url=$manifestUrl check-certificate=yes output=user as-value]
    :if (($manifestResponse->"status") != "finished") do={ :error "auto-ir-ranges: manifest fetch did not finish" }
    :local manifestText ($manifestResponse->"data")
    :if ([:len $manifestText] > $maxPageBytes) do={ :error "auto-ir-ranges: manifest too large" }
    :local manifest [:deserialize from=json value=$manifestText options=json.no-string-conversion]
    :if (($manifest->"schema") != 2) do={ :error "auto-ir-ranges: unsupported manifest schema" }
    :if (($manifest->"country") != "IR") do={ :error "auto-ir-ranges: manifest country is not IR" }
    :local generation ($manifest->"generation")
    :if (([:len $generation] != 64) || ($generation ~ "[^0-9a-f]")) do={ :error "auto-ir-ranges: invalid generation ID" }
    :if (([:len $pendingGeneration] > 0) && ($generation != $pendingGeneration)) do={ :error "auto-ir-ranges: wrong recovery generation" }
    :local generatedAt [:tostr ($manifest->"generated_at")]
    :local metaV4 ($manifest->"ipv4")
    :local metaV6 ($manifest->"ipv6")
    :local expectedV4 ($metaV4->"count")
    :local expectedV6 ($metaV6->"count")
    :if (([:typeof $expectedV4] != "num") || ([:typeof $expectedV6] != "num")) do={ :error "auto-ir-ranges: invalid family counts" }
    :if (($expectedV4 < 1000) || ($expectedV4 > 50000)) do={ :error "auto-ir-ranges: IPv4 capacity/count limit" }
    :if (($expectedV6 < 300) || ($expectedV6 > 50000)) do={ :error "auto-ir-ranges: IPv6 capacity/count limit" }
    :if ([:len $pendingGeneration] > 0) do={
        :if ((($journal->"expected4") != $expectedV4) || (($journal->"expected6") != $expectedV6)) do={ :error "auto-ir-ranges: recovery counts changed" }
    }
    # Budget for desired maps, existing maps and the transient union on disk.
    :local capacityEntries ($expectedV4 + $expectedV6 + $currentV4 + $currentV6)
    :local memoryRequired (16777216 + $capacityEntries * 2048)
    :local storageRequired (4194304 + $capacityEntries * 384)
    :if ([/system resource get free-memory] < $memoryRequired) do={ :error ("auto-ir-ranges: insufficient memory; required=" . $memoryRequired) }
    :if ([/system resource get free-hdd-space] < $storageRequired) do={ :error ("auto-ir-ranges: insufficient storage; required=" . $storageRequired) }
    :local desiredV4 [:toarray ""]
    :local desiredV6 [:toarray ""]
    :local parsedV4 0
    :local parsedV6 0

    :local pagesV4 ($metaV4->"pages")
    :if (([:len $pagesV4] < 1) || ([:len $pagesV4] > 64)) do={ :error "auto-ir-ranges: invalid IPv4 page count" }
    :local pageNumberV4 0
    :local totalBytesV4 0
    :foreach page in=$pagesV4 do={
        :set pageNumberV4 ($pageNumberV4 + 1)
        :local digits ("0000" . $pageNumberV4)
        :local filename ("generations/" . $generation . "/ipv4-" . [:pick $digits ([:len $digits] - 4) [:len $digits]] . ".zone")
        :if ((($page->"number") != $pageNumberV4) || (($page->"family") != 4) || (($page->"generation") != $generation) || (($page->"file") != $filename)) do={ :error "auto-ir-ranges: mixed generation or invalid IPv4 page sequence" }
        :local pageBytes ($page->"bytes")
        :local pageCount ($page->"count")
        :if (([:typeof $pageBytes] != "num") || ([:typeof $pageCount] != "num")) do={ :error "auto-ir-ranges: invalid page metadata" }
        :if (($pageBytes < 1) || ($pageBytes > $maxPageBytes) || ($pageCount < 1) || ($pageCount > $expectedV4)) do={ :error "auto-ir-ranges: page capacity violation" }
        :local response [/tool fetch url=($baseUrl . $filename) check-certificate=yes output=user as-value]
        :if (($response->"status") != "finished") do={ :error "auto-ir-ranges: page download incomplete" }
        :local ipv4Data ($response->"data")
        :if ([:len $ipv4Data] != $pageBytes) do={ :error "auto-ir-ranges: IPv4 byte-size mismatch" }
        :if ([:convert $ipv4Data transform=sha512 to=hex] != ($page->"sha512")) do={ :error "auto-ir-ranges: IPv4 SHA-512 mismatch" }
        :set totalBytesV4 ($totalBytesV4 + $pageBytes)
        :local beforeCount $parsedV4
        :local v4Offset 0
        :local v4Length [:len $ipv4Data]
        :if ([:pick $ipv4Data ($v4Length - 1) $v4Length] != "\n") do={ :error "auto-ir-ranges: missing final newline" }
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
        :if ($cidr ~ "[^0-9a-fA-F:./]") do={ :error "auto-ir-ranges: invalid IPv4 CIDR characters" }
        :local addressPart [:pick $cidr 0 $slash]
        :local prefixLength [:tonum [:pick $cidr ($slash + 1) [:len $cidr]]]
        :if ([:typeof [:toip $addressPart]] != "ip") do={ :error ("auto-ir-ranges: invalid IPv4 address " . $cidr) }
        :if ([:typeof $prefixLength] != "num") do={ :error ("auto-ir-ranges: invalid IPv4 prefix " . $cidr) }
        :if (($prefixLength < 0) || ($prefixLength > 32)) do={ :error ("auto-ir-ranges: invalid IPv4 prefix " . $cidr) }
        :if ($prefixLength = 32) do={ :set cidr $addressPart }
        :if ([:typeof ($desiredV4->$cidr)] != "nothing") do={ :error ("auto-ir-ranges: duplicate IPv4 CIDR " . $cidr) }
        :if ($prefixLength = 32) do={ :set cidr $addressPart }
        :set ($desiredV4->$cidr) true
        :set parsedV4 ($parsedV4 + 1)
    }

        :if (($parsedV4 - $beforeCount) != $pageCount) do={ :error "auto-ir-ranges: IPv4 page count mismatch" }
    }
    :if (($parsedV4 != $expectedV4) || ($totalBytesV4 != ($metaV4->"bytes"))) do={ :error "auto-ir-ranges: IPv4 count mismatch" }
    :if (($parsedV4 * 2) < $baselineV4) do={
        :local proof ($metaV4->"dns_only_shrink")
        :if (([:typeof $proof] != "array") || (($proof->"previous_count") != $baselineV4) || (($proof->"reason") != "removed space covered only by prior DNS observations")) do={ :error "auto-ir-ranges: IPv4 shrink guard" }
        :log warning "auto-ir-ranges: accepting publisher-validated IPv4 DNS-only shrink"
    }

    :local pagesV6 ($metaV6->"pages")
    :if (([:len $pagesV6] < 1) || ([:len $pagesV6] > 64)) do={ :error "auto-ir-ranges: invalid IPv6 page count" }
    :local pageNumberV6 0
    :local totalBytesV6 0
    :foreach page in=$pagesV6 do={
        :set pageNumberV6 ($pageNumberV6 + 1)
        :local digits ("0000" . $pageNumberV6)
        :local filename ("generations/" . $generation . "/ipv6-" . [:pick $digits ([:len $digits] - 4) [:len $digits]] . ".zone")
        :if ((($page->"number") != $pageNumberV6) || (($page->"family") != 6) || (($page->"generation") != $generation) || (($page->"file") != $filename)) do={ :error "auto-ir-ranges: mixed generation or invalid IPv6 page sequence" }
        :local pageBytes ($page->"bytes")
        :local pageCount ($page->"count")
        :if (([:typeof $pageBytes] != "num") || ([:typeof $pageCount] != "num")) do={ :error "auto-ir-ranges: invalid page metadata" }
        :if (($pageBytes < 1) || ($pageBytes > $maxPageBytes) || ($pageCount < 1) || ($pageCount > $expectedV6)) do={ :error "auto-ir-ranges: page capacity violation" }
        :local response [/tool fetch url=($baseUrl . $filename) check-certificate=yes output=user as-value]
        :if (($response->"status") != "finished") do={ :error "auto-ir-ranges: page download incomplete" }
        :local ipv6Data ($response->"data")
        :if ([:len $ipv6Data] != $pageBytes) do={ :error "auto-ir-ranges: IPv6 byte-size mismatch" }
        :if ([:convert $ipv6Data transform=sha512 to=hex] != ($page->"sha512")) do={ :error "auto-ir-ranges: IPv6 SHA-512 mismatch" }
        :set totalBytesV6 ($totalBytesV6 + $pageBytes)
        :local beforeCount $parsedV6
        :local v6Offset 0
        :local v6Length [:len $ipv6Data]
        :if ([:pick $ipv6Data ($v6Length - 1) $v6Length] != "\n") do={ :error "auto-ir-ranges: missing final newline" }
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
        :if ($cidr ~ "[^0-9a-fA-F:./]") do={ :error "auto-ir-ranges: invalid IPv6 CIDR characters" }
        :local addressPart [:pick $cidr 0 $slash]
        :local prefixLength [:tonum [:pick $cidr ($slash + 1) [:len $cidr]]]
        :if ([:typeof [:toip6 $addressPart]] != "ip6") do={ :error ("auto-ir-ranges: invalid IPv6 address " . $cidr) }
        :if ([:typeof $prefixLength] != "num") do={ :error ("auto-ir-ranges: invalid IPv6 prefix " . $cidr) }
        :if (($prefixLength < 0) || ($prefixLength > 128)) do={ :error ("auto-ir-ranges: invalid IPv6 prefix " . $cidr) }
        :if ([:typeof ($desiredV6->$cidr)] != "nothing") do={ :error ("auto-ir-ranges: duplicate IPv6 CIDR " . $cidr) }
        :set ($desiredV6->$cidr) true
        :set parsedV6 ($parsedV6 + 1)
    }

        :if (($parsedV6 - $beforeCount) != $pageCount) do={ :error "auto-ir-ranges: IPv6 page count mismatch" }
    }
    :if (($parsedV6 != $expectedV6) || ($totalBytesV6 != ($metaV6->"bytes"))) do={ :error "auto-ir-ranges: IPv6 count mismatch" }
    :if (($parsedV6 * 2) < $baselineV6) do={
        :local proof ($metaV6->"dns_only_shrink")
        :if (([:typeof $proof] != "array") || (($proof->"previous_count") != $baselineV6) || (($proof->"reason") != "removed space covered only by prior DNS observations")) do={ :error "auto-ir-ranges: IPv6 shrink guard" }
        :log warning "auto-ir-ranges: accepting publisher-validated IPv6 DNS-only shrink"
    }

    # Journal only after every page of BOTH families passed validation.
    :local state {"generation"=$generation;"previous4"=$baselineV4;"previous6"=$baselineV6;"expected4"=$expectedV4;"expected6"=$expectedV6}
    :if ([:len $pendingGeneration] = 0) do={
        /system script set [find where name="auto-ir-ranges-state"] comment=[:serialize to=json value=$state options=json.no-string-conversion]
    }
    # Build current membership maps once. This avoids one RouterOS search per CIDR.
    :local presentV4 [:toarray ""]
    :foreach entryId in=[/ip firewall address-list find where list=$listV4] do={
        :local currentAddress [:tostr [/ip firewall address-list get $entryId address]]
        :set ($presentV4->$currentAddress) true
    }
    :local presentV6 [:toarray ""]
    :foreach entryId in=[/ipv6 firewall address-list find where list=$listV6] do={
        :local currentAddress [:tostr [/ipv6 firewall address-list get $entryId address]]
        :set ($presentV6->$currentAddress) true
    }

    :local addedV4 0
    :local adoptedV4 0
    :local removedV4 0
    :local duplicateV4 0
    :local addedV6 0
    :local adoptedV6 0
    :local removedV6 0
    :local duplicateV6 0

    # Stage every missing CIDR in both families before any removal.
    :foreach cidr,wanted in=$desiredV4 do={
        :if ([:typeof ($presentV4->$cidr)] = "nothing") do={
            /ip firewall address-list add list=$listV4 address=$cidr comment=$managedComment
            :set ($presentV4->$cidr) true
            :set addedV4 ($addedV4 + 1)
        }
    }
    :foreach cidr,wanted in=$desiredV6 do={
        :if ([:typeof ($presentV6->$cidr)] = "nothing") do={
            /ipv6 firewall address-list add list=$listV6 address=$cidr comment=$managedComment
            :set ($presentV6->$cidr) true
            :set addedV6 ($addedV6 + 1)
        }
    }

    # Adopt one desired entry per CIDR, then prune duplicates and stale entries.
    :local seenV4 [:toarray ""]
    :foreach entryId in=[/ip firewall address-list find where list=$listV4] do={
        :local currentAddress [:tostr [/ip firewall address-list get $entryId address]]
        :if ([:typeof ($desiredV4->$currentAddress)] = "nothing") do={
            /ip firewall address-list remove $entryId
            :set removedV4 ($removedV4 + 1)
        } else={
            :if ([:typeof ($seenV4->$currentAddress)] = "nothing") do={
                :set ($seenV4->$currentAddress) true
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

    :local seenV6 [:toarray ""]
    :foreach entryId in=[/ipv6 firewall address-list find where list=$listV6] do={
        :local currentAddress [:tostr [/ipv6 firewall address-list get $entryId address]]
        :if ([:typeof ($desiredV6->$currentAddress)] = "nothing") do={
            /ipv6 firewall address-list remove $entryId
            :set removedV6 ($removedV6 + 1)
        } else={
            :if ([:typeof ($seenV6->$currentAddress)] = "nothing") do={
                :set ($seenV6->$currentAddress) true
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

    :local finalV4 [:len [/ip firewall address-list find where list=$listV4]]
    :local finalV6 [:len [/ipv6 firewall address-list find where list=$listV6]]
    :local ownedV4 [:len [/ip firewall address-list find where list=$listV4 and comment=$managedComment and disabled=no]]
    :local ownedV6 [:len [/ipv6 firewall address-list find where list=$listV6 and comment=$managedComment and disabled=no]]
    :if (($finalV4 != $parsedV4) || ($ownedV4 != $parsedV4)) do={ :error ("auto-ir-ranges: final IPv4 mismatch " . $finalV4 . "/" . $ownedV4 . "/" . $parsedV4) }
    :if (($finalV6 != $parsedV6) || ($ownedV6 != $parsedV6)) do={ :error ("auto-ir-ranges: final IPv6 mismatch " . $finalV6 . "/" . $ownedV6 . "/" . $parsedV6) }

    # A completed marker is written only after both exact counts are verified.
    /system script set [find where name="auto-ir-ranges-state"] comment=""
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
/system scheduler add name="auto-ir-ranges-daily" disabled=yes start-time=03:00:00 interval=1d on-event="auto-ir-ranges-sync" policy=read,write,test comment="managed:mikrotik-auto-ir-ranges version=2.0.0"

:onerror syncError in={
    /system script run auto-ir-ranges-sync
} do={
    :log error ("auto-ir-ranges: initial sync failed; scheduler remains disabled: " . $syncError)
    :error $syncError
}
/system scheduler enable [/system scheduler find where name="auto-ir-ranges-daily"]
:log info "auto-ir-ranges: v2.0.0 installed; daily schedule enabled at 03:00 local time"
