"""The stranger-path CLI wiring (MLB-209).

`--new-public-workbook` is the one entry point that creates its own
destination. Everything asserted here is about it staying in its lane:

  - it never resolves, reads, or writes a configured dev/prod sheet;
  - it authorizes through the PUBLIC profile, and the SAME client both
    creates the workbook and renders into it (under `drive.file` no other
    client may open that file);
  - it refuses flag combinations that would mean two destinations at once;
  - it is not wired for the points-league (CBS) almanac.

Account-free and fully synthetic: the renderer, the publisher, and the
authorizer are all stubs.
"""
from __future__ import annotations

import pytest

import generate_almanac_sheet as gas
import sheets_auth
import sheets_workbook


class _Args:
    """The subset of the parsed namespace these paths read."""

    def __init__(self, new_public_workbook=False, prod=False, no_sheets=False,
                 public_workbook_title=None, new_public_workbook_force=False,
                 confirm_link_sharing=False, advanced_snowflake=False,
                 duckdb=None, preview_dir=None, include_trades=False,
                 print_all=False, season_year=None, matchup_period=None):
        self.new_public_workbook = new_public_workbook
        self.prod = prod
        self.no_sheets = no_sheets
        self.public_workbook_title = public_workbook_title
        self.new_public_workbook_force = new_public_workbook_force
        self.confirm_link_sharing = confirm_link_sharing
        self.advanced_snowflake = advanced_snowflake
        self.duckdb = duckdb
        self.preview_dir = preview_dir
        self.include_trades = include_trades
        self.print_all = print_all
        self.season_year = season_year
        self.matchup_period = matchup_period


class _Parser:
    def error(self, message):          # argparse's contract: never returns
        raise SystemExit(f'parser.error: {message}')


# ---------------------------------------------------------------------------
# Flag validation
# ---------------------------------------------------------------------------

def test_new_public_workbook_and_prod_are_mutually_exclusive():
    """Two answers to 'which workbook?'. Letting one silently win is how
    a stranger-path run ends up rewriting the live league book."""
    with pytest.raises(SystemExit, match='pick one'):
        gas._validate_public_workbook_args(
            _Args(new_public_workbook=True, prod=True), _Parser())


def test_new_public_workbook_and_no_sheets_are_mutually_exclusive():
    with pytest.raises(SystemExit, match='cannot be combined with'):
        gas._validate_public_workbook_args(
            _Args(new_public_workbook=True, no_sheets=True), _Parser())


def test_a_title_without_the_flag_is_rejected_rather_than_ignored():
    with pytest.raises(SystemExit, match='only applies with'):
        gas._validate_public_workbook_args(
            _Args(public_workbook_title='Almanac'), _Parser())


def test_force_without_the_flag_is_rejected_rather_than_ignored():
    with pytest.raises(SystemExit, match='only applies with'):
        gas._validate_public_workbook_args(
            _Args(new_public_workbook_force=True), _Parser())


def test_automation_confirmation_without_public_flag_is_rejected():
    with pytest.raises(SystemExit, match='only applies with'):
        gas._validate_public_workbook_args(
            _Args(confirm_link_sharing=True), _Parser())


def test_snowflake_override_and_duckdb_are_mutually_exclusive():
    with pytest.raises(SystemExit, match='pick one'):
        gas._validate_public_workbook_args(
            _Args(new_public_workbook=True, advanced_snowflake=True,
                  duckdb=True), _Parser())


def test_public_path_forces_default_duckdb_without_a_duckdb_flag(monkeypatch):
    seen = []
    monkeypatch.setattr(gas.db, 'use_duckdb', lambda path: seen.append(path))

    gas._configure_data_source(_Args(new_public_workbook=True))

    assert seen == [None]


def test_advanced_snowflake_is_the_only_public_opt_out_of_duckdb(monkeypatch):
    seen = []
    monkeypatch.setattr(gas.db, 'use_duckdb', lambda path: seen.append(path))

    gas._configure_data_source(
        _Args(new_public_workbook=True, advanced_snowflake=True))

    assert seen == []


def test_the_ordinary_maintainer_invocations_still_validate():
    for args in (_Args(), _Args(prod=True), _Args(no_sheets=True),
                 _Args(new_public_workbook=True),
                 _Args(new_public_workbook=True,
                       public_workbook_title='Almanac',
                       new_public_workbook_force=True)):
        gas._validate_public_workbook_args(args, _Parser())


# ---------------------------------------------------------------------------
# Titles
# ---------------------------------------------------------------------------

def test_the_default_title_carries_no_league_or_owner_identity():
    """It is the first thing a stranger sees in their own Drive."""
    title = gas.safe_workbook_title(None)
    assert title == gas.DEFAULT_PUBLIC_WORKBOOK_TITLE
    assert title == 'Fantasy League Almanac'


@pytest.mark.parametrize('raw,expected', [
    ('  My   Almanac  ', 'My Almanac'),
    ('Almanac\nwith\tcontrol', 'Almanacwithcontrol'),
    ('', 'Fantasy League Almanac'),
    ('   ', 'Fantasy League Almanac'),
])
def test_titles_are_normalised(raw, expected):
    assert gas.safe_workbook_title(raw) == expected


def test_a_very_long_title_is_capped():
    assert len(gas.safe_workbook_title('x' * 500)) == 120


# ---------------------------------------------------------------------------
# The publish path
# ---------------------------------------------------------------------------

@pytest.fixture
def no_target_resolution(monkeypatch):
    """Any read of a configured dev/prod sheet is a test failure."""
    def _explode(*a, **kw):
        raise AssertionError(
            'the stranger path resolved a configured Sheets target')

    monkeypatch.setattr(gas.sheets_target, 'resolve_sheets_target', _explode)


@pytest.fixture
def stub_publish(monkeypatch):
    """Capture what publish_workbook would have been asked to do."""
    seen = {}

    def _publish(client, title, render, resume=True, confirm_share=None):
        seen['client'] = client
        seen['title'] = title
        seen['resume'] = resume
        seen['confirm_share'] = confirm_share
        render('created-sheet-id')
        assert confirm_share is not None
        seen['confirmed'] = confirm_share()
        return sheets_workbook.PublishResult(
            spreadsheet_id='created-sheet-id', url='THE-URL', title=title,
            created=True, rendered=True, shared=True)

    seen['publish'] = _publish
    return seen


@pytest.fixture
def stub_render(monkeypatch):
    calls = []
    monkeypatch.setattr(
        gas.almanac_sheets, 'write_almanac',
        lambda sheet_id, **kw: calls.append((sheet_id, kw)))
    return calls


def test_the_publish_path_never_resolves_a_configured_target(
        no_target_resolution, stub_publish, stub_render, capsys):
    gas.publish_new_public_workbook(
        _Args(new_public_workbook=True), 2026, 7,
        client='PUBLIC-CLIENT', publish=stub_publish['publish'],
        input_fn=lambda prompt: 'YES')

    assert stub_publish['title'] == 'Fantasy League Almanac'


def test_the_renderer_gets_the_new_id_and_the_public_client(
        no_target_resolution, stub_publish, stub_render):
    gas.publish_new_public_workbook(
        _Args(new_public_workbook=True), 2026, 7,
        client='PUBLIC-CLIENT', publish=stub_publish['publish'],
        input_fn=lambda prompt: 'YES')

    assert len(stub_render) == 1
    sheet_id, kwargs = stub_render[0]
    assert sheet_id == 'created-sheet-id'
    assert kwargs['client'] == 'PUBLIC-CLIENT', (
        'the render used a different client than the one that created the '
        'workbook -- under drive.file it would not be able to open it'
    )
    assert kwargs['season_year'] == 2026
    assert kwargs['matchup_period'] == 7


def test_authorization_goes_through_the_public_profile(
        no_target_resolution, stub_publish, stub_render, monkeypatch):
    asked = []
    monkeypatch.setattr(
        sheets_auth, 'authorized_client',
        lambda profile: asked.append(profile) or 'PUBLIC-CLIENT')

    gas.publish_new_public_workbook(
        _Args(new_public_workbook=True), 2026, 7,
        publish=stub_publish['publish'], input_fn=lambda prompt: 'YES')

    assert asked == [sheets_auth.PUBLIC]
    assert stub_publish['client'] == 'PUBLIC-CLIENT'


def test_a_custom_title_reaches_the_publisher(
        no_target_resolution, stub_publish, stub_render):
    gas.publish_new_public_workbook(
        _Args(new_public_workbook=True, public_workbook_title='  My  Book '),
        2026, 7, client='C', publish=stub_publish['publish'],
        input_fn=lambda prompt: 'YES')

    assert stub_publish['title'] == 'My Book'


def test_force_turns_resume_off(no_target_resolution, stub_publish,
                                stub_render):
    gas.publish_new_public_workbook(
        _Args(new_public_workbook=True, new_public_workbook_force=True),
        2026, 7, client='C', publish=stub_publish['publish'],
        input_fn=lambda prompt: 'YES')
    assert stub_publish['resume'] is False

    gas.publish_new_public_workbook(
        _Args(new_public_workbook=True), 2026, 7,
        client='C', publish=stub_publish['publish'],
        input_fn=lambda prompt: 'YES')
    assert stub_publish['resume'] is True


def test_the_share_ready_line_is_printed_on_the_happy_path(
        no_target_resolution, stub_publish, stub_render, capsys):
    gas.publish_new_public_workbook(
        _Args(new_public_workbook=True), 2026, 7,
        client='C', publish=stub_publish['publish'],
        input_fn=lambda prompt: 'YES')

    out = capsys.readouterr().out
    assert sheets_workbook.SHARE_READY_LINE.format(url='THE-URL') in out


def test_link_sharing_requires_the_exact_affirmative_answer(
        no_target_resolution, stub_publish, stub_render):
    gas.publish_new_public_workbook(
        _Args(new_public_workbook=True), 2026, 7,
        client='C', publish=stub_publish['publish'],
        input_fn=lambda prompt: 'y')
    assert stub_publish['confirmed'] is False

    gas.publish_new_public_workbook(
        _Args(new_public_workbook=True), 2026, 7,
        client='C', publish=stub_publish['publish'],
        input_fn=lambda prompt: 'YES')
    assert stub_publish['confirmed'] is True


def test_automation_flag_is_an_explicit_affirmative_without_a_prompt(
        no_target_resolution, stub_publish, stub_render):
    def _no_prompt(prompt):
        raise AssertionError('automation flag still prompted')

    gas.publish_new_public_workbook(
        _Args(new_public_workbook=True, confirm_link_sharing=True), 2026, 7,
        client='C', publish=stub_publish['publish'], input_fn=_no_prompt)
    assert stub_publish['confirmed'] is True


def test_sharing_disclosure_names_the_information_and_public_audience():
    text = sheets_workbook.LINK_SHARING_DISCLOSURE
    for phrase in ('league and team names', 'member/display names',
                   'standings', 'matchups', 'scores', 'rosters',
                   'draft results', 'transactions',
                   'anyone who receives the link'):
        assert phrase in text


def test_a_policy_blocked_publish_prints_no_success_line(
        no_target_resolution, stub_render, capsys):
    def _blocked(client, title, render, resume=True, confirm_share=None):
        render('created-sheet-id')
        assert confirm_share()
        return sheets_workbook.PublishResult(
            spreadsheet_id='created-sheet-id', url='THE-URL', title=title,
            created=True, rendered=True, shared=False,
            share_error='Drive refused',
            recovery=sheets_workbook.SHARE_RECOVERY_MESSAGE)

    gas.publish_new_public_workbook(
        _Args(new_public_workbook=True), 2026, 7,
        client='C', publish=_blocked, input_fn=lambda prompt: 'YES')

    out = capsys.readouterr().out
    assert '-- share-ready.' not in out
    assert 'NOT share-ready' in out
    assert 'THE-URL' in out


def test_a_points_league_reaches_the_same_public_workbook_path(monkeypatch):
    """MLB-243 REVERSES the old refusal.

    This used to assert that `--new-public-workbook` errors for a points
    league. That refusal was written when "points league" meant "CBS", and
    once the dispatch started reading the canonical format it became the
    blocker on the actual stranger journey: the rehearsal league is an ESPN
    season-points league, and refusing it would leave the volunteer with no
    workbook at all.

    So the assertion inverts -- a points league publishes -- and what must
    NOT change is the security path it publishes through. That is the next
    test.
    """
    monkeypatch.setattr('sys.argv',
                        ['generate_almanac_sheet.py', '--new-public-workbook'])
    monkeypatch.setattr(gas.db, 'set_league', lambda key: None)
    monkeypatch.setattr(gas.db, 'use_duckdb', lambda path=None: None)
    monkeypatch.setattr(gas.league_format, 'resolve',
                        lambda *a, **kw: gas.league_format.POINTS)
    monkeypatch.setattr(gas.db, 'league', lambda: _EspnLeague())

    published = []
    monkeypatch.setattr(
        gas, 'publish_new_public_workbook',
        lambda args, season, period, **kw: published.append(kw))

    gas.main()

    assert published, 'a points league did not reach the public publish path'
    assert callable(published[0]['render']), (
        'the points path must supply its own renderer rather than falling '
        'back to the H2H almanac'
    )


def test_the_points_public_path_uses_the_points_renderer(monkeypatch):
    """FORMAT decides what gets drawn. The points league must render the
    points workbook into the app-created book, not the H2H one."""
    monkeypatch.setattr(gas.db, 'league', lambda: _EspnLeague())
    monkeypatch.setattr(gas.league_format, 'resolve',
                        lambda *a, **kw: gas.league_format.POINTS)

    rendered = []
    monkeypatch.setattr(gas.points_almanac, 'write_points_almanac',
                        lambda sheet_id, client=None:
                            rendered.append((sheet_id, client)))

    def _h2h(*a, **kw):
        raise AssertionError('the H2H almanac rendered into a points workbook')

    monkeypatch.setattr(gas.almanac_sheets, 'write_almanac', _h2h)

    captured = {}
    monkeypatch.setattr(
        gas, 'publish_new_public_workbook',
        lambda args, season, period, **kw: captured.update(kw))
    gas._run_points_league_almanac(
        _Args(new_public_workbook=True), _Parser())

    captured['render']('SHEET-ID', 'PUBLIC-CLIENT')
    assert rendered == [('SHEET-ID', 'PUBLIC-CLIENT')], (
        'the points renderer did not receive the app-created workbook id '
        'and the public drive.file client'
    )


def test_the_points_public_path_keeps_the_oauth_boundary(monkeypatch):
    """The renderer changed; the SECURITY PATH must not have.

    Same PUBLIC profile, same client threaded into the render, same
    create -> render -> confirm -> share ordering. A points league needed a
    different workbook shape, never a different way of obtaining a
    workbook, and this is the test that stops the two from being confused
    again.
    """
    events = []

    class _Client:
        pass

    client = _Client()

    monkeypatch.setattr(gas.sheets_auth, 'authorized_client',
                        lambda profile: events.append(('auth', profile)) or client)
    monkeypatch.setattr(gas.sheets_auth, 'consent_disclosure',
                        lambda profile: 'DISCLOSURE')

    def _publish(cl, title, render, resume=True, confirm_share=None, **kw):
        assert cl is client, 'the publisher got a different client than the '\
                             'one the public profile authorized'
        events.append(('create', title))
        render('NEW-ID')
        events.append(('confirm', bool(confirm_share and confirm_share())))
        events.append(('share', 'NEW-ID'))
        return sheets_workbook.PublishResult(
            spreadsheet_id='NEW-ID', url='U', title=title,
            created=True, rendered=True, shared=True)

    monkeypatch.setattr(gas.sheets_workbook, 'publish_workbook', _publish)
    monkeypatch.setattr(gas.sheets_target, 'resolve_sheets_target',
                        lambda *a, **kw: (_ for _ in ()).throw(
                            AssertionError('a configured sheet was resolved')))

    rendered = []
    gas.publish_new_public_workbook(
        _Args(new_public_workbook=True, confirm_link_sharing=True),
        None, None,
        render=lambda sheet_id, cl: rendered.append((sheet_id, cl)),
    )

    assert events[0] == ('auth', sheets_auth.PUBLIC), (
        'the points path did not authorize through the PUBLIC drive.file '
        'profile'
    )
    assert [e[0] for e in events[1:]] == ['create', 'confirm', 'share'], (
        f'the publish sequence changed for the points format: {events}'
    )
    assert rendered == [('NEW-ID', client)], (
        'the points renderer did not get the app-created id and the public '
        'client'
    )


class _EspnLeague:
    """Minimal stand-in for the registry League object."""

    key = 'espn-main'
    platform = 'espn'
    display_name = 'ESPN main league'


def test_the_ledger_default_never_points_at_a_test_path(monkeypatch, tmp_path):
    """The publisher's default ledger resolves at call time, so a test can
    redirect it -- which is also what keeps the suite from writing into
    output/ when it exercises the real publisher."""
    monkeypatch.setattr(sheets_workbook, 'LEDGER_PATH', tmp_path / 'wb.json')
    assert sheets_workbook.WorkbookLedger().path == tmp_path / 'wb.json'
