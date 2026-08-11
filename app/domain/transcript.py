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
"""

from pydantic import BaseModel, ConfigDict, Field


class TranscriptTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speaker: str
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

    def render_for_prompt(self) -> str:
        """Flattens to the text the LLM sees, one turn per line.

        The leading `[mm:ss]` is what makes `evidence_type: "dialogue"`
        answerable — without it the model is asked for a timestamp it has no
        way to read off the input.
        """
        return "\n".join(f"[{turn.timestamp}] {turn.speaker}: {turn.text}" for turn in self.turns)
