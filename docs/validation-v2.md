# v2 validation — 2026-09-17

The accepted operator/provider/service catalogue is unchanged in scope. The
change adds systematic discovery, explicit review evidence, resilient collection
and scalable delivery. It does not claim that pending leads are already covered.

## Offline and source validation

- 48 tests pass on local Python and GitHub Actions Python 3.13.
- Fixtures cover seed-free Arvan foreign-affiliate nomination, an unfamiliar
  operator, naming collisions, unrelated holders and shared-hosting boundaries.
- Community lineage does not inflate corroboration; unsupported domain rules
  remain visible. All three initial community adapters complete against pinned
  upstream snapshots with license texts retained.
- The live global index contains 87,004 ASNs. Removing the whole manual operator
  catalogue still nominates AS208006 and AS57568 from systematic Iranian anchors.
- DNS tests cover individual resolver/family failures, disagreement, successive
  outages, original timestamps/TTLs, successful empty answers, endpoint changes
  and the 48-hour generation cutoff.
- Page tests reject missing/corrupt/mixed pages, capacity violations and duplicate
  or malformed content. Legacy files freeze as a complete generation when needed.
- IP/ASN explanations expose source and review evidence and reject mismatched
  generation provenance.
- Paired baseline/candidate generation using the same live public inputs produced
  exactly the same 1,818 IPv4 and 585 IPv6 prefixes, with zero address-space change.
  All admitted sources pass exact containment. Global coverage and lab recall
  remain unmeasured here; unresolved services/discovery are separate dimensions.

## Disposable RouterOS

Fresh official CHR 7.23.7 under QEMU, local HTTPS with a disposable CA, loopback
SSH/API only. The production aliases were not used by the integration harnesses.

`tests/routeros_lab.py` verified:

- Complete multi-page install and exact membership, with stable entry IDs on a
  repeated run.
- Same-length corrupted pages (SHA-512 failure), missing final pages and mixed
  generations leave both lists unchanged.
- Entry ceiling, insufficient memory and insufficient storage fail before changes.
- Interruption during the add phase preserves prior entries; recovery finishes
  the journal's immutable generation even after the latest pointer changes.
- Interruption during pruning preserves every desired address; rerun finishes.

`tests/routeros_capacity_lab.py` additionally installed exactly 5,100 IPv4 and
900 IPv6 entries. The IPv4 family was 67,920 bytes, larger than a single RouterOS
fetch result, with pages near the full 48 KiB ceiling. The verified DNS-only
expiry path then reduced it to 1,100/350; non-DNS source loss remained blocked.
Both publication and the updater were idempotent afterward.

The 50,000-per-family policy ceiling is enforced and admission is gated by
memory/storage estimates. This is not a benchmark claiming every RouterOS device
can sustain 100,000 entries. Physical power-loss durability was not simulated;
the tests interrupt actual application at both mutation phases and resume using
the persistent journal.

## rush canary

RouterOS 7.23.7, ARM64 Chateau Pro ax. Before installation, save an AES-encrypted
binary backup, a sensitive-hidden export, both full list snapshots, owned
updater/scheduler state and 11 routing/firewall configuration sections privately.
The backup password stays in the private local validation directory.

Canary data commit: `f3dd87f` on `codex/coverage-v2-canary-data`.
Generation: `7695b6bbdf5364f6747691626106b1b048ac38d754eaf830e1408070306cd653`.

- Independent HTTPS download and full page validation passed before installation.
- Installed membership exactly equals all 1,818 IPv4 and 585 IPv6 prefixes.
- Repeat sync preserved every managed entry and its ID.
- All 11 routing/firewall exports remained equal after removing export timestamps.
- All other address-list contents and the 11 separate Arvan entries were retained.
  Two unrelated `local-kingdom-6` timeout counters refreshed through their existing
  15-second scheduler; comparisons exclude those volatile countdown values.
- The recovery journal cleared and the 03:00 daily updater was enabled.
- The original evaluation-lab worktree and capture implementation were left intact.

Private backups, raw router exports, keys and lab observations are not published
in this repository. The weekly investigator is activated after merge and may
propose PRs only. Separate Arvan-list removal remains deferred.
