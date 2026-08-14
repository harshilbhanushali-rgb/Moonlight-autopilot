"""The legacy-row marker.

The property worth a test: it changes `status` and nothing else. The four output
columns are the A/B baseline for every rubric measurement taken so far, and they
also hold the evidence of what the pipeline produced for a call with no
conversation in it.
"""

from app.db.models import STATUS_EXCLUDED, STATUS_PROCESSED
from scripts.mark_excluded import mark_excluded_analyses


class FakeSession:
    def __init__(self, rows):
        self._rows = rows
        self.updates = []
        self.commits = 0

    def execute(self, statement):
        if statement.is_select:
            return _Result(self._rows)
        self.updates.append(statement.compile().params)
        return None

    def commit(self):
        self.commits += 1


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


# (recording_id, status, call_type, call_score, card_type, excluded_reason)
PROCESSED_BUT_EXCLUDED = ("a", STATUS_PROCESSED, "Discovery", "Low", "Coaching", "no_conversation")
ALREADY_MARKED = ("b", STATUS_EXCLUDED, "Discovery", "Low", "Coaching", "no_client_speech")


def test_a_processed_row_for_an_excluded_call_is_marked():
    session = FakeSession([PROCESSED_BUT_EXCLUDED])

    summary = mark_excluded_analyses(session=session)

    assert (summary.marked, summary.already_marked) == (1, 0)
    assert session.updates[0]["status"] == STATUS_EXCLUDED
    assert session.commits == 1


def test_only_the_status_column_is_written():
    session = FakeSession([PROCESSED_BUT_EXCLUDED])

    mark_excluded_analyses(session=session)

    written = session.updates[0]
    # The A/B baseline must survive: nothing but status may appear here.
    for column in ("call_type", "call_score", "risk_gap_analysis", "card_type"):
        assert column not in written


def test_an_already_marked_row_is_not_rewritten():
    session = FakeSession([ALREADY_MARKED])

    summary = mark_excluded_analyses(session=session)

    assert (summary.marked, summary.already_marked) == (0, 1)
    assert session.updates == []
    assert session.commits == 0


def test_a_dry_run_writes_nothing():
    session = FakeSession([PROCESSED_BUT_EXCLUDED])

    summary = mark_excluded_analyses(session=session, dry_run=True)

    assert summary.marked == 1
    assert session.updates == []
    assert session.commits == 0


def test_nothing_to_do_is_not_an_error():
    session = FakeSession([])

    summary = mark_excluded_analyses(session=session)

    assert (summary.marked, summary.already_marked) == (0, 0)
    assert session.commits == 0
