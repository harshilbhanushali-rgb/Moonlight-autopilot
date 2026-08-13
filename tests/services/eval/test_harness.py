from app.domain.transcript import Transcript, TranscriptSpeaker, TranscriptTurn
from app.domain.types import CallType, Gap
from app.services.eval.harness import GapModeComparison, compare_gap_modes
from app.llm.client import StubLLMClient
from app.prompts.registry import PromptRegistry


def transcript(text):
    return Transcript(
        speakers=[TranscriptSpeaker(id=0, name="rep", email="rep@joveo.com", is_rep=True)],
        turns=[TranscriptTurn(speaker="rep", speaker_id=0, text=text, start_s=0)],
    )


def make_registry(tmp_path):
    demo_dir = tmp_path / "gap_rubric" / "demo"
    demo_dir.mkdir(parents=True)
    (demo_dir / "v1-descriptiononly.yaml").write_text("desc rubric", encoding="utf-8")
    (demo_dir / "v1-fewshot.yaml").write_text("fewshot rubric", encoding="utf-8")
    return PromptRegistry(root=tmp_path)


async def test_compare_gap_modes_runs_both_modes_for_each_transcript(tmp_path):
    registry = make_registry(tmp_path)
    llm = StubLLMClient(
        responses={
            "gap_analysis": (
                '{"gaps": [{"theme": "T", "evidence_type": "explanation", '
                '"evidence": "E", "timestamp": null, "confidence": "medium"}]}'
            )
        }
    )

    results = await compare_gap_modes(
        llm_client=llm,
        transcripts=[transcript("transcript one"), transcript("transcript two")],
        call_type=CallType.DEMO,
        registry=registry,
    )

    expected_gap = Gap(theme="T", evidence_type="explanation", evidence="E", confidence="medium")
    assert len(results) == 2
    for r in results:
        assert isinstance(r, GapModeComparison)
        assert r.descriptiononly_gaps == [expected_gap]
        assert r.fewshot_gaps == [expected_gap]
        assert r.descriptiononly_prompt_hash != r.fewshot_prompt_hash


async def test_compare_gap_modes_never_writes_to_the_analysis_table():
    import inspect

    from app.services.eval import harness

    source = inspect.getsource(harness)
    assert "app.db.models" not in source
    assert "Analysis" not in source
