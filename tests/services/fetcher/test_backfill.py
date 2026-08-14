"""The backfill's failure handling.

The load-bearing property: a row Avoma can no longer serve is left exactly as it
was. Overwriting it with anything partial would destroy a transcript that cannot
be recovered — 19 of the 51 stored calls are no longer listed in
`moonlight_calls`, so our own table is their only index.
"""

from app.avoma.client import AvomaSpeaker, AvomaTranscript, AvomaTranscriptTurn
from app.core.input_gate_config import InputGateConfig
from scripts.backfill import backfill_transcripts

GATE_ON = InputGateConfig(enabled=True, min_words=300, require_client_speech=True)

REP = AvomaSpeaker(id=0, email="rep@joveo.com", name="Rep", is_rep=True)
CLIENT = AvomaSpeaker(id=1, email="buyer@acme.com", name="Buyer", is_rep=False)


def transcript(*turn_specs, speakers=(REP, CLIENT), uuid="m1"):
    return AvomaTranscript(
        meeting_uuid=uuid,
        uuid="t1",
        speakers=list(speakers),
        turns=[
            AvomaTranscriptTurn(
                speaker_id=sid, text=" ".join(["word"] * words), timestamps=[float(i + 1)]
            )
            for i, (sid, words) in enumerate(turn_specs)
        ],
        transcription_vtt_url=None,
    )


class FakeSession:
    """Records the UPDATE statements the backfill issues."""

    def __init__(self, recording_ids):
        self._recording_ids = recording_ids
        self.updates = []
        self.commits = 0

    def execute(self, statement):
        if statement.is_select:
            return _Result(self._recording_ids)
        self.updates.append(statement.compile().params)
        return None

    def commit(self):
        self.commits += 1


class _Result:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return self

    def all(self):
        return list(self._values)


class FakeAvoma:
    def __init__(self, by_id):
        self._by_id = by_id
        self.requested = []

    def get_transcript_by_meeting_uuid(self, recording_id):
        self.requested.append(recording_id)
        value = self._by_id.get(recording_id, KeyError)
        if value is KeyError:
            return None
        if isinstance(value, Exception):
            raise value
        return value


def run(by_id, recording_ids, **kwargs):
    session = FakeSession(recording_ids)
    summary = backfill_transcripts(
        our_session=session,
        avoma_client=FakeAvoma(by_id),
        input_gate_config=GATE_ON,
        **kwargs,
    )
    return summary, session


def test_a_recoverable_row_is_rewritten_with_speaker_identity():
    summary, session = run({"a": transcript((0, 200), (1, 200))}, ["a"])

    assert (summary.updated, summary.excluded) == (1, 0)
    written = session.updates[0]
    assert written["transcript"]["speakers"]
    assert written["transcript"]["turns"][0]["speaker_id"] == 0
    assert written["excluded_reason"] is None


def test_a_row_avoma_no_longer_serves_is_left_untouched():
    summary, session = run({}, ["gone"])

    assert (summary.missing_from_avoma, summary.updated) == (1, 0)
    assert session.updates == []


def test_an_avoma_error_leaves_the_row_untouched_and_does_not_abort_the_run():
    summary, session = run(
        {"a": RuntimeError("boom"), "b": transcript((0, 200), (1, 200))}, ["a", "b"]
    )

    assert summary.missing_from_avoma == 1
    assert summary.updated == 1
    assert len(session.updates) == 1


def test_an_unusable_shape_is_counted_and_skipped():
    # No speakers at all: transcript_to_storage_shape refuses it.
    summary, session = run({"a": transcript((0, 400), speakers=())}, ["a"])

    assert (summary.malformed, summary.updated) == (1, 0)
    assert session.updates == []


def test_the_gate_is_re_evaluated_so_history_gets_its_reason_too():
    summary, session = run({"a": transcript((0, 20), speakers=(REP,))}, ["a"])

    assert summary.excluded == 1
    written = session.updates[0]
    assert written["excluded_reason"] == "no_conversation"
    assert "20 words" in written["excluded_detail"]


def test_a_dry_run_writes_nothing_but_still_reports_what_would_change():
    summary, session = run(
        {"a": transcript((0, 20), speakers=(REP,))}, ["a"], dry_run=True
    )

    assert summary.updated == 1
    assert summary.excluded == 1
    assert session.updates == []
    assert session.commits == 0


def test_each_row_is_committed_separately_so_a_late_failure_keeps_earlier_work():
    ids = ["a", "b", "c"]
    summary, session = run({i: transcript((0, 200), (1, 200)) for i in ids}, ids)

    assert summary.updated == 3
    assert session.commits == 3


def test_limit_stops_after_n_rows():
    ids = ["a", "b", "c"]
    summary, _ = run({i: transcript((0, 200), (1, 200)) for i in ids}, ids, limit=2)

    assert summary.considered == 2
