"""
Resolve which Google Sheet the output scripts write to.

v1.3 introduced a dev/prod split so the almanac and records surfaces can
be iterated against a throwaway testing sheet without touching the live
league sheet. MLB-58 makes the resolution per-league: each registry entry
(config/leagues.yml) declares its own sheet destinations under `sinks`,
as the NAMES of .env variables holding the spreadsheet ids (ids stay out
of the public repo -- the registry's quasi-privacy convention):

    sinks:
      sheets_almanac_env: CBS_SHEETS_OUTPUT_ID   # production sheet
      sheets_dev_env:     CBS_SHEETS_DEV_ID      # dev/testing sheet

The target is chosen at the CLI:

    (default)  -> the league's dev / testing sheet
    --prod     -> the league's production sheet

Design intent, unchanged from v1.3: the *safe* target (dev) is the
default, and writing to production is always an explicit, typed choice.
An explicit --prod with no production sheet configured raises rather
than silently no-opping, so a real deploy never quietly does nothing --
and per MLB-58, a league without a configured sink for a requested
surface fails loudly BEFORE anything is generated. A missing dev sheet
stays preview-only (the test-safety convention: the suite never writes
to live destinations).

Legacy single-league form: calling without a league keeps the exact
pre-registry chain (SHEETS_PROD_ID > SHEETS_OUTPUT_ID for prod,
SHEETS_DEV_ID for dev). The SHEETS_PROD_ID override also still wins for
any league whose production sink IS the legacy SHEETS_OUTPUT_ID pair --
the v1.3 migration contract, honored until that variable disappears
from .env files.
"""

import os


def resolve_sheets_target(use_prod, league=None):
    """Return ``(sheet_id, label)`` for the requested target.

    ``league`` is a ``config.league_registry.League`` (scripts pass
    ``db.league()`` after ``db.set_league``); ``None`` selects the legacy
    single-league chain. ``label`` is ``'dev'`` or ``'PROD'`` (for
    logging). In the dev case ``sheet_id`` may be ``None`` -- when the
    dev variable is unset/empty or the league declares no dev sink --
    which callers treat as "preview only".

    Raises ``RuntimeError`` when ``use_prod`` is True but no production
    sheet resolves, so an explicit ``--prod`` can never silently skip a
    real deploy.
    """
    if league is None:
        return _resolve_legacy(use_prod)

    sinks = league.sinks or {}
    prod_env = sinks.get('sheets_almanac_env')
    dev_env = sinks.get('sheets_dev_env')

    if use_prod:
        # v1.3 migration shim: the global SHEETS_PROD_ID override applies
        # exactly where the legacy pair applies (the league whose prod
        # sink is the pre-registry SHEETS_OUTPUT_ID).
        if prod_env == 'SHEETS_OUTPUT_ID' and os.getenv('SHEETS_PROD_ID'):
            return os.getenv('SHEETS_PROD_ID'), 'PROD'
        if not prod_env:
            raise RuntimeError(
                f"League '{league.key}' has no production sheet configured "
                f"for the almanac surface. Set sinks.sheets_almanac_env in "
                f"config/leagues.yml to the name of a .env variable holding "
                f"the spreadsheet id."
            )
        sheet_id = os.getenv(prod_env)
        if not sheet_id:
            raise RuntimeError(
                f"League '{league.key}': sinks.sheets_almanac_env names "
                f"{prod_env!r} but that variable is unset or empty. Add it "
                f"to the .env at the repo root."
            )
        return sheet_id, 'PROD'

    if not dev_env:
        # No dev sink declared: preview-only, same convention as an unset
        # dev variable. Deliberately NOT an error -- dev is the safe path.
        return None, 'dev'
    return (os.getenv(dev_env) or None), 'dev'


def _resolve_legacy(use_prod):
    """The pre-registry single-league chain, byte-for-byte."""
    if use_prod:
        sheet_id = os.getenv('SHEETS_PROD_ID') or os.getenv('SHEETS_OUTPUT_ID')
        if not sheet_id:
            raise RuntimeError(
                '--prod requested but no production sheet is configured. '
                'Set SHEETS_PROD_ID (or the legacy SHEETS_OUTPUT_ID) in .env.'
            )
        return sheet_id, 'PROD'

    # Empty string -> None so callers can use a simple `if not sheet_id`.
    return (os.getenv('SHEETS_DEV_ID') or None), 'dev'
