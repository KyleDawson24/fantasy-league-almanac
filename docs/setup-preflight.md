# Setup preflight (development rung)

The first v2.0 onboarding rung is now a read-only ESPN preflight. It validates
the league ID, both private-league cookies, every requested season, the served
league name and team count, and enough platform evidence to choose a supported
points workbook format before the long history run.

Run it from an activated Python 3.13 environment:

```powershell
python tools/setup_league.py
```

The cookie prompts are hidden. The command does not accept secrets as command
line arguments, print them, write `.env`, edit `config/leagues.yml`, download
league data, open Google, or start the almanac build. A successful result is a
validated profile for the next wizard rung; the v1.9.1 quickstart remains the
supported manual configuration journey until that writer and handoff land.

Leave the final-season prompt blank for an ongoing league. The preflight checks
through the current season while preserving `final_season: null` for the future
registry writer, so the league does not become accidentally frozen at today's
year.

Failure messages use stable categories for bad input, expired authentication,
denied access, unavailable history, network failures, unexpected ESPN
responses, and unsupported/undetermined formats. The validation logic lives in
`config/bootstrap.py`; this CLI is only one UI shell over it.
