# Multi-Platform Support

**Linear:** [Multi-Platform Support](https://linear.app/roguelitedevelopment/project/multi-platform-support-aaaa5e9aa79f) · Backlog · Medium

Per-platform rollouts once Platform Abstraction defines the contract. One
Linear milestone per platform (CBS · Yahoo · Platform #3 pending recon), each
starting with an access spike — what can this login actually see? — before
any code.

Data assets in hand: a login to a 20-year CBS league (father-in-law's;
archive depth and keeper status unknown — if the history is accessible it is
a spectacular test corpus) and a dormant 4-team Yahoo dummy league (messy is
fine for API-shape recon). Platform-landscape recon runs first: verify
whether Sleeper even offers season-long MLB (it is football-first; baseball
support is the open question) and assess Fantrax (the deep/dynasty baseball
standard) and Ottoneu before committing the #3 slot.

## Recon findings (2026-07-07, MLB-12)

| Platform | Season-long MLB? | Data access | Auth |
|---|---|---|---|
| ESPN | ✓ (current adapter) | unofficial (`espn-api` + raw views) | session cookies |
| Yahoo | ✓ (#2 mainstream) | official, documented Fantasy Sports API | OAuth2 app |
| CBS | ✓ (paid commissioner OG, deep archives) | official Developer API v3.0; community token tooling | league-scoped token |
| Fantrax | ✓ (deep/dynasty standard, ~1M+ claimed) | community access by league ID (`fantraxapi`-class libs) | league ID (+ session) |
| Sleeper | **✗ — DFS picks only; no season-long MLB** | n/a for baseball | — |
| Ottoneu | ✓ (salary-cap niche) | limited/exports | — |

Recommended: Sleeper off the board; **Platform #3 milestone → Fantrax**
(pending maintainer sign-off); #2 stays CBS-vs-Yahoo, decided by the
access spikes — CBS holds the data asset (the 20-year league), Yahoo the
cleanest official API. Ottoneu only on user demand.

**Depends on:** Platform Abstraction (the contract); access spikes can run
anytime.

**Seeded issues:** MLB-12 platform recon (findings above) · MLB-13 CBS
access spike · MLB-14 Yahoo access spike
