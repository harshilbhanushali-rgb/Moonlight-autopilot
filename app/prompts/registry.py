from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

_VERSION_MODE_RE = re.compile(r"^(?P<label>[^-]+)(?:-(?P<mode>.+))?$")
_PROMPT_SOURCE_EXTENSIONS = {".txt", ".yaml", ".yml"}
_CALL_TYPE_SCOPED_KINDS = {"gap_rubric", "scoring"}


def _is_ignored_dir(name: str) -> bool:
    return name.startswith("__") or name.startswith(".")


def _is_prompt_source_file(path: Path) -> bool:
    return path.suffix in _PROMPT_SOURCE_EXTENSIONS


@dataclass(frozen=True)
class PromptFile:
    kind: str
    label: str
    content: str
    content_hash: str
    call_type: str | None = None
    mode: str | None = None


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def load_prompt_file(
    path: Path, *, kind: str, call_type: str | None = None
) -> PromptFile:
    content = path.read_text(encoding="utf-8")
    match = _VERSION_MODE_RE.match(path.stem)
    label = match.group("label") if match else path.stem
    mode = match.group("mode") if match else None
    return PromptFile(
        kind=kind,
        call_type=call_type,
        mode=mode,
        label=label,
        content=content,
        content_hash=_hash(content),
    )


def _version_sort_key(label: str) -> tuple[int, str]:
    digits = re.sub(r"\D", "", label)
    return (int(digits) if digits else -1, label)


class PromptRegistry:
    """Loads versioned prompt/rubric files from disk.

    Layout convention:
      <root>/<kind>/<label>.<ext>                          (call_type, card_type, gap_fill)
      <root>/<kind>/<call_type>/<label>[-<mode>].<ext>      (call-type-scoped: gap_rubric,
                                                              optionally mode-scoped; scoring)
    """

    def __init__(self, root: Path):
        self._root = Path(root)
        self._files = list(self._discover())

    def _discover(self):
        if not self._root.is_dir():
            return
        for kind_dir in self._root.iterdir():
            if not kind_dir.is_dir() or _is_ignored_dir(kind_dir.name):
                continue
            kind = kind_dir.name
            if kind in _CALL_TYPE_SCOPED_KINDS:
                for call_type_dir in kind_dir.iterdir():
                    if not call_type_dir.is_dir() or _is_ignored_dir(call_type_dir.name):
                        continue
                    for file_path in call_type_dir.iterdir():
                        if file_path.is_file() and _is_prompt_source_file(file_path):
                            yield load_prompt_file(
                                file_path, kind=kind, call_type=call_type_dir.name
                            )
            else:
                for file_path in kind_dir.iterdir():
                    if file_path.is_file() and _is_prompt_source_file(file_path):
                        yield load_prompt_file(file_path, kind=kind)

    def all(
        self, *, kind: str, call_type: str | None = None, mode: str | None = None
    ) -> list[PromptFile]:
        return [
            f
            for f in self._files
            if f.kind == kind
            and (call_type is None or f.call_type == call_type)
            and (mode is None or f.mode == mode)
        ]

    def latest(
        self, *, kind: str, call_type: str | None = None, mode: str | None = None
    ) -> PromptFile:
        matches = self.all(kind=kind, call_type=call_type, mode=mode)
        if not matches:
            raise KeyError(f"no prompts found for kind={kind!r} call_type={call_type!r} mode={mode!r}")
        return max(matches, key=lambda f: _version_sort_key(f.label))

    def get(
        self,
        *,
        kind: str,
        label: str,
        call_type: str | None = None,
        mode: str | None = None,
    ) -> PromptFile:
        for f in self.all(kind=kind, call_type=call_type, mode=mode):
            if f.label == label:
                return f
        raise KeyError(
            f"no prompt found for kind={kind!r} label={label!r} call_type={call_type!r} mode={mode!r}"
        )
