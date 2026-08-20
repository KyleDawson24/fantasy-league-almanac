# Release Notes -- v2.0.1

v2.0.1 is a release-hygiene and documentation patch. The guided ESPN-to-Google-Workbook journey shipped in v2.0.0 is unchanged.

## Public tree and bundle hygiene

The current public tree and release ZIP no longer carry the maintainer's Reddit launch draft, release ceremony checklist, or PII-review disposition ledger. Those are working process artifacts rather than stranger-facing product documentation.

The two Markdown checklists remain in the maintainer's gitignored local workspace. The disposition ledger now lives beside the existing private PII map and HMAC salt under the same local custody and backup policy. The strict pre-push guard still refuses missing inventory, missing salt, new unreviewed occurrences, and real-league identifiers; this patch changes where its private review state lives, not what the safety gate proves.

The v2.0.0 tag and Git history are intentionally not rewritten. Anyone deliberately inspecting an earlier tag can still find the former files, but they are no longer presented in the current repository tree or included in the latest consumer download.

## Advanced league-configuration CSVs

The documentation now counts all 14 CSV templates under `dbt_league/league_config/` and states their actual role in the guided product. The ESPN wizard does not ask for or populate them: ordinary ESPN identity and matchup scheduling are derived from platform data.

Thirteen templates are connected to dbt or an advanced workflow. They provide CBS historian inputs or sparse owner, franchise, player-identity, matchup-date, and abnormal-period corrections when a league needs them; `dbt seed` loads populated rows automatically. `cbs_early_anchors_backfill.csv` is the one exception: it remains a hand-entered worklist, and no model reads it today.

Every template still ships header-only. No real league configuration, owner information, or override value is included in source or in the release ZIP.

## Runtime impact

There is no extraction, transform, workbook, credential, OAuth, sharing, or guided-setup behavior change in v2.0.1. A user who already downloaded v2.0.0 does not need this patch for correctness; new testers should use v2.0.1 so the public package contains only the intended product and contributor documentation.
