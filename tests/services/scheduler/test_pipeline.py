from datetime import date

from app.services.scheduler import pipeline


def test_run_daily_pipeline_skips_everything_when_not_claimed(monkeypatch):
    calls = []
    monkeypatch.setattr(pipeline, "get_session", lambda: _FakeSession())
    monkeypatch.setattr(pipeline.ledger, "claim_run", lambda session, job_name, run_date: None)
    monkeypatch.setattr(pipeline.fetcher_run, "main", lambda **_: calls.append("fetcher"))
    monkeypatch.setattr(pipeline.batch_run, "main", lambda: calls.append("batch"))

    pipeline.run_daily_pipeline(today=date(2026, 1, 1))

    assert calls == []


def test_run_daily_pipeline_runs_fetcher_then_batch_and_marks_completed(monkeypatch):
    calls = []
    completed = []
    monkeypatch.setattr(pipeline, "get_session", lambda: _FakeSession())
    monkeypatch.setattr(pipeline.ledger, "claim_run", lambda session, job_name, run_date: 42)
    monkeypatch.setattr(pipeline.ledger, "mark_completed", lambda session, run_id: completed.append(run_id))
    monkeypatch.setattr(pipeline.fetcher_run, "main", lambda **_: calls.append("fetcher"))
    monkeypatch.setattr(pipeline.batch_run, "main", lambda: calls.append("batch"))

    pipeline.run_daily_pipeline(today=date(2026, 1, 1))

    assert calls == ["fetcher", "batch"]
    assert completed == [42]


def test_run_daily_pipeline_skips_batch_when_fetcher_fails(monkeypatch):
    calls = []
    failures = []
    monkeypatch.setattr(pipeline, "get_session", lambda: _FakeSession())
    monkeypatch.setattr(pipeline.ledger, "claim_run", lambda session, job_name, run_date: 42)
    monkeypatch.setattr(
        pipeline.ledger, "mark_failed", lambda session, run_id, error: failures.append((run_id, error))
    )

    def failing_fetcher(**_):
        raise RuntimeError("avoma is down")

    monkeypatch.setattr(pipeline.fetcher_run, "main", failing_fetcher)
    monkeypatch.setattr(pipeline.batch_run, "main", lambda: calls.append("batch"))

    pipeline.run_daily_pipeline(today=date(2026, 1, 1))

    assert calls == []
    assert failures == [(42, "avoma is down")]


def test_run_daily_pipeline_marks_failed_when_batch_fails_after_successful_fetch(monkeypatch):
    calls = []
    failures = []
    monkeypatch.setattr(pipeline, "get_session", lambda: _FakeSession())
    monkeypatch.setattr(pipeline.ledger, "claim_run", lambda session, job_name, run_date: 42)
    monkeypatch.setattr(
        pipeline.ledger, "mark_failed", lambda session, run_id, error: failures.append((run_id, error))
    )
    monkeypatch.setattr(pipeline.fetcher_run, "main", lambda **_: calls.append("fetcher"))

    def failing_batch():
        raise RuntimeError("llm gateway timeout")

    monkeypatch.setattr(pipeline.batch_run, "main", failing_batch)

    pipeline.run_daily_pipeline(today=date(2026, 1, 1))

    assert calls == ["fetcher"]
    assert failures == [(42, "llm gateway timeout")]


class _FakeSession:
    def close(self):
        pass
