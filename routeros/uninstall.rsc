# mikrotik-auto-ir-ranges v2.0.0 uninstaller
# Keeps Iran_IPV4, Iran_IPV6, and every rule that references them.

:if ([:len [/system scheduler find where name="auto-ir-ranges-daily"]] > 0) do={
    /system scheduler remove [/system scheduler find where name="auto-ir-ranges-daily"]
}
:if ([:len [/system script find where name="auto-ir-ranges-sync"]] > 0) do={
    /system script remove [/system script find where name="auto-ir-ranges-sync"]
}
# Recovery journal is retained so a later reinstall can safely finish an interrupted sync.
:log info "auto-ir-ranges: updater removed; Iran address lists retained"
