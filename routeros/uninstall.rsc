# mikrotik-auto-ir-ranges v2.0.1 uninstaller
# Keeps Iran_IPV4, Iran_IPV6, and every rule that references them.

:if ([:len [/system scheduler find where name="auto-ir-ranges-daily"]] > 0) do={
    /system scheduler remove [/system scheduler find where name="auto-ir-ranges-daily"]
}
:if ([:len [/system script find where name="auto-ir-ranges-sync"]] > 0) do={
    /system script remove [/system script find where name="auto-ir-ranges-sync"]
}
# Pending recovery files are retained for a later reinstall.
:local legacyState [/system script find where name="auto-ir-ranges-state"]
:if ([:len $legacyState] > 0) do={
    :if (([:len [/system script get $legacyState source]] = 0) && ([:len [/system script get $legacyState comment]] = 0)) do={ /system script remove $legacyState }
}
:log info "auto-ir-ranges: updater removed; Iran address lists retained"
