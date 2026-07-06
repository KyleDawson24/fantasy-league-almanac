# Player Profiles

**Linear:** [Player Profiles](https://linear.app/roguelitedevelopment/project/player-profiles-76a3625ac4d1) · Backlog · Low (deliberately parked)

Player-as-entity surfaces: career-with-team stats, acquisition history,
callout appearances, records held. The data bricks mostly exist or are
planned (`fct_player_season_performance`, `fct_player_position_pts`, the
pre-Linear roadmap's `dim_player` + `fct_player_career`); Transaction
Records adds the acquisition story.

Parked until a front-facing view exists (Public Dashboard or the web app) —
navigation is unenvisionable without one, and cards nobody can reach are
shelf-ware. Architecture rule carried from BRAINTHOUGHTS: the profile is a
consumer-side assembly of independent bricks, never a mega-model.

**Depends on:** a front surface (Public Dashboard or Self-Serve Web App);
enriched by Transaction Records.

**Seeded issues:** MLB-20 brick inventory + navigation sketch
