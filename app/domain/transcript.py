"""The `call_storage.transcript` contract.

Previously this shape existed only as a docstring, and both sides read it with
`.get(key, default)` — so when the Call Fetcher silently stopped carrying turn
timestamps through, nothing failed: the analyser just rendered timestamp-free
text, and the gap step invented `mm:ss` values that looked plausible and were
wrong. Making it an explicit model means a turn that can't be anchored to a
moment is rejected at the boundary instead of quietly producing unanchorable
prompts.

`start_s` is therefore required, not optional. A transcript in call_storage is
always citable, or it isn't stored.

`speakers` and `speaker_id` are required for the same reason, one layer up: the
input gate asks whether anyone on the client's side actually *spoke*, and that
question is only answerable if each turn is attributable to an identified
person. Carrying identity in the loose `call_metadata` dict instead was
considered and rejected — a gate whose input silently goes missing does not fail
loudly, it just stops rejecting anything and the corpus looks clean.

The display label on a turn is deliberately kept alongside `speaker_id`:
`render_for_prompt` still emits exactly `[mm:ss] Speaker: text`, so adding
identity changed no prompt text and moved no analyser output.
"""

from pydantic import BaseModel, ConfigDict, Field


class TranscriptSpeaker(BaseModel):
    """One participant Avoma resolved for this call.

    `name` and `email` are nullable because Avoma does not always match a
    diarized voice to a person — but the keys are required, so a writer that
    stops supplying them fails validation rather than degrading quietly.

    `is_rep` is Avoma's own flag for "belongs to our side". Measured over 51
    real calls it agrees with `joveo.com` membership on every speaker, which is
    why the gate uses it instead of comparing email domains against the
    account's domain — see `app/domain/input_gate.py`.
    """

    model_config = ConfigDict(extra="forbid")

    id: int
    name: str | None
    email: str | None
    is_rep: bool


class TranscriptTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speaker: str
    speaker_id: int
    text: str
    start_s: float = Field(ge=0, description="Seconds from the start of the call")

    @property
    def timestamp(self) -> str:
        """`start_s` as mm:ss — the format the gap rubric asks the model to cite."""
        whole_seconds = int(self.start_s)
        return f"{whole_seconds // 60:02d}:{whole_seconds % 60:02d}"


class Transcript(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turns: list[TranscriptTurn]
    speakers: list[TranscriptSpeaker]

    def render_for_prompt(self) -> str:
        """Flattens to the text the LLM sees, one turn per line.

        The leading `[mm:ss]` is what makes `evidence_type: "dialogue"`
        answerable — without it the model is asked for a timestamp it has no
        way to read off the input.
        """
        return "\n".join(f"[{turn.timestamp}] {turn.speaker}: {turn.text}" for turn in self.turns)
