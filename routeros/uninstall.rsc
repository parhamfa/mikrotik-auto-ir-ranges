# mikrotik-auto-ir-ranges v2.1.0 uninstaller
# Keeps Iran_IPV4, Iran_IPV6, and every rule that references them.

:if ([:len [/system scheduler find where name="auto-ir-ranges-daily"]] > 0) do={
    /system scheduler remove [/system scheduler find where name="auto-ir-ranges-daily"]
}
:if ([:len [/system script find where name="auto-ir-ranges-sync"]] > 0) do={
    /system script remove [/system script find where name="auto-ir-ranges-sync"]
}
# Remove leftovers of older versions; best effort, never fails the uninstall.
:foreach leftover in={"auto-ir-ranges/state.json";"auto-ir-ranges/state.json.tmp";"flash/auto-ir-ranges/state.json";"flash/auto-ir-ranges/state.json.tmp"} do={
    :do { /file remove [/file find where name=$leftover] } on-error={}
}
:foreach leftover in={"auto-ir-ranges";"flash/auto-ir-ranges"} do={
    :do {
        :if ([:len [/file find where name~("^" . $leftover . "/")]] = 0) do={ /file remove [/file find where name=$leftover and type=directory] }
    } on-error={}
}
:do { /system script remove [/system script find where name="auto-ir-ranges-state"] } on-error={}
:log info "auto-ir-ranges: updater removed; Iran address lists retained"
