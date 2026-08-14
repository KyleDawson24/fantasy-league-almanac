"""The transaction-capture contract (MLB-243).

THE BUG. `fetch_transactions` had two return values for four situations:
`[]` meant both "the board was read and held nothing" and "ESPN serves no
board for this season", and `None` meant "refused" -- decided by the FIRST
401, with no retry and no request timeout.

Downstream, all three of those produced zero rows in `RAW.TRANSACTIONS`, and
`fct_roster_stints` gated itself on rows existing. So a perfectly ordinary
quiet league -- read successfully, genuinely no activity -- got zero roster
stints and no acquisition analysis, indistinguishable from a league we were
locked out of.

It is worse than that in practice: the feed FLAPS. Identical well-formed
requests against the rehearsal league minutes apart returned
401 / 200 / 200 / 200 / 401 / 200 while mTeam and mRoster stayed 200
throughout. One flap and a league with 121 real topics captured nothing.

Pure: `requests.get` is stubbed everywhere here. No network, no warehouse.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("LEAGUE_ID", "0")

_spec = importlib.util.spec_from_file_location(
    "extract_transaction_coverage_under_test", _REPO_ROOT / "extract" / "extract.py")
extract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(extract)


class _Response:
    def __init__(self, status_code, topics=None):
        self.status_code = status_code
        self._topics = topics

    def json(self):
        return {'topics': self._topics or []}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(
                f'raise_for_status reached for HTTP {self.status_code}; the '
                f'classifier should have handled it')


def _stub(monkeypatch, responses):
    """Serve `responses` in order; record the kwargs of every call."""
    calls = []
    queue = list(responses)

    def _get(url, **kwargs):
        calls.append(kwargs)
        return queue.pop(0) if queue else _Response(200, [])

    monkeypatch.setattr(extract.requests, 'get', _get)
    monkeypatch.setattr(extract.time, 'sleep', lambda s: None)
    return calls


# ---------------------------------------------------------------------------
# The four outcomes
# ---------------------------------------------------------------------------

def test_a_served_nonempty_log_is_captured(monkeypatch):
    _stub(monkeypatch, [_Response(200, [{'id': 1}, {'id': 2}])])
    capture = extract.fetch_transactions(2026)
    assert capture.outcome == extract.SERVED_NONEMPTY
    assert capture.served is True
    assert capture.topic_count == 2


def test_a_served_empty_log_is_AUTHORITATIVE_not_unavailable(monkeypatch):
    """The case the old code could not express. HTTP 200 with nothing in it
    is a measurement, and the only thing that can prove a quiet league."""
    _stub(monkeypatch, [_Response(200, [])])
    capture = extract.fetch_transactions(2026)
    assert capture.outcome == extract.SERVED_EMPTY
    assert capture.served is True, 'a read board is served, empty or not'
    assert capture.topic_count == 0
    assert capture.topics == [], 'a served-empty capture still has a payload'


def test_an_unauthorized_log_is_unavailable_with_no_payload(monkeypatch):
    _stub(monkeypatch, [_Response(401)] * extract.TRANSACTION_AUTH_RETRIES)
    capture = extract.fetch_transactions(2026)
    assert capture.outcome == extract.UNAUTHORIZED
    assert capture.served is False
    assert capture.topics is None, (
        'an unavailable feed must not carry [], which reads downstream as '
        'proven-zero activity'
    )
    assert capture.topic_count is None


def test_a_forbidden_log_is_also_unavailable(monkeypatch):
    _stub(monkeypatch, [_Response(403)] * extract.TRANSACTION_AUTH_RETRIES)
    assert extract.fetch_transactions(2026).outcome == extract.UNAUTHORIZED


def test_a_historical_404_is_NOT_SERVED_never_an_authoritative_zero(monkeypatch):
    """ESPN serves the board for the current season only. "No board exists"
    is not "nothing happened" -- the old code returned [] for both."""
    _stub(monkeypatch, [_Response(404)])
    capture = extract.fetch_transactions(2026)
    assert capture.outcome == extract.NOT_SERVED
    assert capture.served is False
    assert capture.topics is None
    assert capture.outcome != extract.SERVED_EMPTY


# ---------------------------------------------------------------------------
# The flap
# ---------------------------------------------------------------------------

def test_a_transient_401_followed_by_200_classifies_as_served(monkeypatch):
    """THE MEASURED FAILURE. One flap used to lose a 121-topic season."""
    _stub(monkeypatch, [
        _Response(401), _Response(401), _Response(200, [{'id': 1}]),
    ])
    capture = extract.fetch_transactions(2026)
    assert capture.outcome == extract.SERVED_NONEMPTY
    assert capture.topic_count == 1
    assert capture.attempts >= 3, 'the retries should be reported'


def test_a_transient_401_before_an_empty_board_still_proves_zero(monkeypatch):
    _stub(monkeypatch, [_Response(401), _Response(200, [])])
    assert extract.fetch_transactions(2026).outcome == extract.SERVED_EMPTY


def test_retries_are_bounded(monkeypatch):
    """A stable refusal must terminate, not hammer ESPN forever."""
    calls = _stub(monkeypatch, [_Response(401)] * 50)
    capture = extract.fetch_transactions(2026)
    assert capture.outcome == extract.UNAUTHORIZED
    assert len(calls) == extract.TRANSACTION_AUTH_RETRIES


def test_a_404_is_not_retried(monkeypatch):
    """It is a definitive answer on the first ask; retrying wastes time and
    hides nothing."""
    calls = _stub(monkeypatch, [_Response(404)] * 10)
    extract.fetch_transactions(2026)
    assert len(calls) == 1


def test_every_request_carries_a_finite_timeout(monkeypatch):
    """A hung capture is its own outage."""
    calls = _stub(monkeypatch, [_Response(200, [])])
    extract.fetch_transactions(2026)
    assert calls, 'no request was made'
    for kwargs in calls:
        assert kwargs.get('timeout'), 'a request went out with no timeout'
        assert kwargs['timeout'] == extract.TRANSACTION_TIMEOUT_SECONDS


# ---------------------------------------------------------------------------
# What reaches the sink
# ---------------------------------------------------------------------------

class _Sink:
    def __init__(self):
        self.transactions = []
        self.coverage = []

    def write_transactions(self, topics, year, league_key):
        self.transactions.append((topics, year, league_key))

    def write_transaction_coverage(self, evidence, year, league_key):
        self.coverage.append((evidence, year, league_key))


def _run(monkeypatch, capture):
    monkeypatch.setattr(extract, 'fetch_transactions', lambda year: capture)
    sink = _Sink()
    extract.extract_transactions(sink, 2026, 'espn-main')
    return sink


def test_coverage_is_written_on_every_outcome(monkeypatch):
    """Including the failures -- those rows are the entire point."""
    for outcome in (extract.SERVED_NONEMPTY, extract.SERVED_EMPTY,
                    extract.UNAUTHORIZED, extract.NOT_SERVED):
        topics = [{'id': 1}] if outcome == extract.SERVED_NONEMPTY else (
            [] if outcome == extract.SERVED_EMPTY else None)
        sink = _run(monkeypatch, extract.TransactionCapture(
            outcome, topics, 200, 1))
        assert len(sink.coverage) == 1, f'no coverage row for {outcome}'
        assert sink.coverage[0][0]['outcome'] == outcome


def test_an_empty_served_board_still_writes_its_snapshot(monkeypatch):
    """The row itself is the proof the board was read and held nothing."""
    sink = _run(monkeypatch, extract.TransactionCapture(
        extract.SERVED_EMPTY, [], 200, 1))
    assert sink.transactions == [([], 2026, 'espn-main')]


def test_an_unavailable_attempt_writes_no_snapshot(monkeypatch):
    """PRESERVING A PRIOR GOOD CAPTURE. The table is append-only, so writing
    nothing leaves the earlier snapshot standing and staging keeps reading
    it. Writing an empty payload here would erase a real log."""
    for outcome in (extract.UNAUTHORIZED, extract.NOT_SERVED):
        sink = _run(monkeypatch, extract.TransactionCapture(outcome, None,
                                                            401, 5))
        assert sink.transactions == [], (
            f'{outcome} wrote a snapshot and would clobber a prior capture'
        )
        assert sink.coverage, 'the failure was not recorded at all'


def test_an_unavailable_capture_never_reports_a_topic_count(monkeypatch):
    """A count of 0 beside an unavailable outcome would be read as zero
    activity by anything scanning the evidence."""
    for outcome in (extract.UNAUTHORIZED, extract.NOT_SERVED):
        evidence = extract.TransactionCapture(outcome, None, 401, 3).as_evidence()
        assert evidence['topic_count'] is None
        assert evidence['outcome'] == outcome


def test_a_sink_without_the_coverage_writer_still_extracts(monkeypatch):
    """The writer is looked up defensively so an older sink cannot break a
    capture that would otherwise succeed."""
    class _Old:
        def __init__(self):
            self.transactions = []

        def write_transactions(self, topics, year, league_key):
            self.transactions.append(topics)

    monkeypatch.setattr(extract, 'fetch_transactions',
                        lambda year: extract.TransactionCapture(
                            extract.SERVED_NONEMPTY, [{'id': 1}], 200, 1))
    sink = _Old()
    extract.extract_transactions(sink, 2026, 'espn-main')
    assert sink.transactions == [[{'id': 1}]]


def test_the_coverage_table_is_part_of_the_raw_contract():
    """It must exist PRESENT AND EMPTY on a fresh install, or the new
    staging model fails resolving a missing relation."""
    import json
    contract = json.loads(
        (_REPO_ROOT / 'config' / 'raw_schema_contract.json').read_text())
    assert 'TRANSACTION_COVERAGE' in contract['tables']
    assert 'TRANSACTION_COVERAGE' in extract.CONDITIONAL_RAW_TABLES


def test_served_outcomes_are_exactly_the_two_read_states():
    assert extract.SERVED_OUTCOMES == {extract.SERVED_NONEMPTY,
                                       extract.SERVED_EMPTY}
    assert extract.UNAUTHORIZED not in extract.SERVED_OUTCOMES
    assert extract.NOT_SERVED not in extract.SERVED_OUTCOMES
