# league_config/ -- whose league this is

Every file here is **your** configuration. On a fresh clone they are blank
templates: a header row and nothing else. Fill in the ones your league
needs, run `dbt seed`, and the pipeline starts describing your league
instead of nobody's.

> **Once filled in, these files hold your league's private data** -- real
> names, and the platform's own member ids -- so do not `git add` them; a
> safer ignored-folder flow is coming (MLB-204).

This directory is deliberately separate from `../seeds/`. Those files are
reference vocabulary -- stat maps, MLB team abbreviations, which stats are
record-worthy. They are the same for every league on a given platform, so
they ship as real content and you should not have to touch them.

    ../seeds/        the same for everyone      -- ships filled in
    league_config/   different for everyone     -- ships blank

## Two kinds of file in here

The distinction matters more than it looks, because it decides what
happens when you leave a file blank.

**REQUIRED -- blank means the surfaces that need it come out empty.**
These carry the facts nothing else can supply: who your franchises and
owners are. A model reads them directly, so an empty file is not a
neutral default, it is an absence.

`matchup_schedule.csv` **used to head this table and no longer does**
(MLB-235). An ESPN league's weeks come from ESPN's own `mMatchupScore`
response, captured on every box-score run, and the dates follow from it
by arithmetic -- ESPN's scoring periods are days, so scoring period N is
the season's first scoring date plus N-1 days, and that opening date is
MLB's published regular-season start, fetched from the free public MLB
Stats API. The file survives as an optional correction and labelling
surface, listed below with the other optional ones.

| file | what it supplies | blank means |
|---|---|---|
| `cbs_franchises.csv` | CBS franchise id -> name/abbrev | CBS franchises unnamed |
| `cbs_team_owners.csv` | CBS franchise -> current owner | no CBS current-owner display |
| `team_owner_by_year.csv` | who owned which franchise, per season | no CBS owner history |
| `draft_assembly_plan.csv` | which draft recording to trust per season | no draft recap |
| `cbs_early_anchors_backfill.csv` | hand-entered early-season anchors | nothing -- see note below |

**OPTIONAL -- blank means identity.** These only ever rename, merge, or
repoint something. Every consumer reaches them through a `left join` and a
`coalesce`, so an empty file means "change nothing", and the pipeline runs
correctly with all of them empty. That is verified, not assumed: see
`tests/test_league_config_templates.py`.

| file | what it changes | blank means |
|---|---|---|
| `owner_nicknames.csv` | how an owner's name displays | platform's own spelling |
| `owner_alias.csv` | two owner ids are one person | ids stay distinct |
| `franchise_lineage.csv` | two franchise ids are one franchise | ids stay distinct |
| `player_nicknames.csv` | how a player's name displays | real name |
| `player_alias.csv` | a name form -> a specific MLB player | normal matching |
| `player_identity_overrides.csv` | force a name form to an MLB id | normal matching |
| `player_identity_context_overrides.csv` | same, scoped to one team-season | normal matching |

## Worked examples

One per file. `league_key` is whatever you named your league in
`config/leagues.yml` (convention: `<platform>-<short-slug>`); the examples
below use `cbs-myleague` and `espn-myleague`.

### The two that started this

> *"we call Brandon Marsh the Greasy Bastard"*

`player_nicknames.csv` -- `player_id,player_name,nickname`

    664285,Brandon Marsh,The Greasy Bastard

`player_id` is the platform's id for that player. `player_name` is there
so the row is readable by a human; the join is on the id. Every downstream
display becomes `COALESCE(nickname, player_name)`, so this one row renames
him everywhere at once.

> *"team id 5 is actually the same as team id 9"*

`franchise_lineage.csv` --
`league_key,franchise_id,season_year,canonical_franchise_id,canonical_name,canonical_abbrev`

    cbs-myleague,9,,5,Lake Effect,LAKE

Franchise 9 is franchise 5 under an older id. Leaving `season_year` blank
applies the merge across all seasons; setting it applies the merge to that
season only. `canonical_name` / `canonical_abbrev` are optional -- blank
means "use whatever the anchor franchise is already called".

This is also the one file whose contents are *history*, not preference. If
your league has never renumbered a team, leave it empty.

### The rest

`matchup_schedule.csv` --
`season_year,matchup_period,start_date,end_date,is_abnormal,abnormal_reason,is_playoff,playoff_round`

    2026,1,2026-03-25,2026-04-05,false,,false,

One row per matchup period per season. **Optional, and normally blank.**
An ESPN league's week membership and dates are both derived now, so a row
here is a *correction or a label*, not a source: an explicit override of a
derived date for a commissioner-declared oddity, or human text the platform
does not serve. `abnormal_reason` is the clearest case -- the derivation can
say a week ran 14 scoring periods, but only a person can write "All-Star
break (14 days)", and that written reason is preferred over the generated
one wherever both agree the week was abnormal. Playoff weeks set
`is_playoff` and name the round; ESPN's captured `scheduleSettings` supplies
the regular-season boundary on its own when it has been captured.

Note that `is_abnormal` here is now the *last* word consulted, not the
first: an explicit `matchup_period_overrides.csv` row beats it, and the
platform-derived verdict beats it too. That ordering is deliberate -- a
hand-maintained flag should not silently outrank what the platform says.

`owner_nicknames.csv` -- `owner_id,first_name,last_name,preferred_name`

    {A1B2C3D4-0000-0000-0000-000000000000},Dana,Okonkwo,Dane

`owner_id` is the platform's member id. `preferred_name`, when set, wins
verbatim -- which is how deliberate casing survives (`McAvery` stays
`McAvery`). Leave it blank and the display falls back to a title-cased
`first_name last_name`.

**These four columns are the whole seed.** If you keep extra columns of
your own in a local copy of this file, dbt loads them and infers their
types, but nothing in the project reads them and no tracked config names
them. That is the intended arrangement, not a loophole.

`owner_alias.csv` -- `league_key,owner_id,canonical_owner_id,preferred_name`

    cbs-myleague,OWNER-OLD-77,OWNER-NEW-12,Dane

Someone re-registered and came back with a new id. The left column is the
id to fold away; `canonical_owner_id` is the one to keep.

`team_owner_by_year.csv` -- `league_key,season_year,franchise_id,owner_name`

    cbs-myleague,2019,5,Dana Okonkwo

The per-season owner record for platforms that do not serve one. This is
where a league's own memory goes when the platform has forgotten it.

`cbs_franchises.csv` -- `league_key,franchise_id,abbrev,franchise_name`

    cbs-myleague,5,LAKE,Lake Effect

`cbs_team_owners.csv` -- `league_key,franchise_id,owner_id`

    cbs-myleague,5,OWNER-NEW-12

`draft_assembly_plan.csv` --
`league_key,season_year,part_seq,draft_key,view,part_role,order_tier,note`

    cbs-myleague,2026,1,2026-draft,live,main,,

One row per part of a season's draft. Most seasons are a single row; a
season that ran two drafts (an auction plus a supplemental, say) gets one
row per part, ordered by `part_seq`.

`player_alias.csv` -- `name_key,mlbam_id,note`

    jr chalas,691406,1990s roster pages wrote him without the periods

A name form your league's records use that automatic matching does not
resolve. `mlbam_id` is the player's MLB Stats API id.

`player_identity_overrides.csv` -- `platform,cbs_name_key,mlbam_id,mlbam_name,note`

    cbs,j gonzalez,114932,Juan Gonzalez,two Gonzalezes in 2004; this one is Juan

The stronger version of `player_alias`: it repoints a name form upstream
of everything, including display names.

`player_identity_context_overrides.csv` --
`league_key,season_year,franchise_id,cbs_name_key,mlbam_id,note`

    cbs-myleague,2004,5,j gonzalez,114932,only this team's 2004 roster

The same override, scoped to one team's one season -- for when the same
name form means different people on different rosters.

### A note on `cbs_early_anchors_backfill.csv`

No model reads this file today. It is a hand-entry worklist kept in the
seed format so it can become one later
(`docs/decisions/CBS_EARLY_ANCHORS_BACKFILL.md`). Leave it blank; nothing
observes whether you do.

## Seeing it work before you fill anything in

You do not have to configure a league to see what the project produces.
The demo fixture is a complete, self-consistent fake league:

```bash
tools/demo.sh
```

That points `DBT_LEAGUE_CONFIG` at `demo/league_config/` for the duration
of the run, so it never reads or writes anything in this directory. Full
setup instructions are in `SETUP.md`.
