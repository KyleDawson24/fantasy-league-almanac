"""
Resolve which Google Sheet the output scripts write to.

v1.3 introduces a dev/prod split so the almanac and records surfaces can
be iterated against a throwaway testing sheet without touching the live
league sheet. The target is chosen at the CLI:

    (default)  -> development / testing sheet, from SHEETS_DEV_ID
    --prod     -> production sheet, from SHEETS_PROD_ID
                  (falls back to the legacy SHEETS_OUTPUT_ID so existing
                  .env files keep working through the migration)

Design intent: the *safe* target (dev) is the default, and writing to
production is always an explicit, typed choice. An explicit --prod with
no production sheet configured raises rather than silently no-opping, so
a real deploy never quietly does nothing.
"""

import os


def resolve_sheets_target(use_prod):
    """Return ``(sheet_id, label)`` for the requested target.

    ``label`` is ``'dev'`` or ``'PROD'`` (for logging). In the dev case
    ``sheet_id`` may be ``None`` -- when SHEETS_DEV_ID is unset/empty --
    which callers treat as "preview only", preserving the historical
    no-op-when-unset behavior.

    Raises ``RuntimeError`` when ``use_prod`` is True but neither
    SHEETS_PROD_ID nor the legacy SHEETS_OUTPUT_ID is set, so an explicit
    ``--prod`` can never silently skip a real deploy.
    """
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
