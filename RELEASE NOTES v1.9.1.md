# Release Notes -- v1.9.1

**ESPN auction drafts now get an auction recap instead of being presented as snake drafts.**

v1.9.0 could run an auction league, but its Draft Recap silently treated ESPN's served array order as pick order. That invented rounds, overall picks, slot analysis and bust/steal grades while omitting the information that actually matters in an auction: what each team bought and how much it paid. This focused patch fixes that presentation without changing the snake-draft path or the rest of the workbook.

Patch rather than minor. There is no migration step; rerunning the normal public command captures the additional draft fields and rebuilds the workbook.

---

## What changed

The extractor now preserves ESPN's auction evidence, including `bidAmount` and `nominatingTeamId`, from RAW through staging and the reporting mart. Auction detection comes from captured draft settings or actual bid evidence, not from the platform name.

Draft Recap renders auction seasons as a deterministic purchase ledger ordered by team and player. It shows the acquiring team and the supplied purchase price. A missing historical price is labelled `Unavailable`; it is never converted to `$0`. The nominating team is retained as evidence but is not mistaken for the team that acquired the player.

Auction seasons no longer receive snake-only analysis. The recap suppresses overall-pick and round meaning, Top Pick, slot analysis, value deltas, bust/steal grades, the snake all-time board and draft-board color grading. Snake drafts with no auction evidence keep the existing board and grading path unchanged.

Historical ESPN seasons use the same contract. If an older endpoint supplies prices, they render. If it does not, the recap says so rather than manufacturing order or value.

---

## What was proved

The completed-draft rehearsal used a public 10-team ESPN auction with a $260 budget and no keepers. ESPN served 190 purchases with prices on all 190. Every purchase survived extraction and modeling exactly once; all 190 reconciled on player, price, acquiring team and nominating team.

The distinction between buyer and nominator mattered: they differed on 152 of 190 purchases, and every modeled and rendered row remained attached to the acquiring team. Prices ranged from $1 to $114. Duplicate prices existed, and reversing the input order produced byte-identical output because presentation ordering is deterministic.

The same live league also exposed an important acquisition boundary before the draft ended. ESPN served a fully populated 190-row placeholder skeleton while reporting `drafted=false`; after completion the structure remained the same and only the completion flag changed. The existing wrapper gate correctly refused the active draft. Nonempty rows alone are not accepted as proof that an auction is finished.

Focused auction and snake regression coverage passed, as did the full default suite. The isolated auction mart build passed its uniqueness and not-null checks. No Snowflake or Google write was part of the rehearsal.

---

## Known boundary

The volunteer league that prompted this patch begins in 2015 and is private, so its older ESPN auction-price coverage has not yet been measured. The release handles that uncertainty explicitly: available prices render, unavailable prices say `Unavailable`, and neither case is converted into fictional snake-pick analysis.

The first live rehearsal league had completed its draft but had no player-result facts yet. Its Draft Recap was therefore validated independently of the full almanac, which correctly refused to invent season results or a league format from empty performance data.
