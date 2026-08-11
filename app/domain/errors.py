class LLMOutputError(Exception):
    """Raised when an LLM response can't be parsed/validated into the expected
    structured output for a reasoning step. Never substitute a deterministic
    fallback for this — the caller should record the step as failed and let
    it be retried."""

    def __init__(self, step: str, raw_content: str, detail: str):
        self.step = step
        self.raw_content = raw_content
        self.detail = detail
        super().__init__(f"{step}: {detail} (raw={raw_content!r})")
