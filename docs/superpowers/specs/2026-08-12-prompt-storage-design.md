# Prompt storage and loading — design

**Date:** 2026-08-12
**Status:** approved, not implemented

## Why

`app/prompts/` and `PromptRegistry` have four defects. All four were hit in
practice on 2026-08-12 while rewriting two gap-rubric themes; none is
hypothetical.

### 1. File comments are part of the prompt

`load_prompt_file` calls `path.read_text()` and the whole string becomes the
system message. Every `#` line in a prompt file is therefore sent to the model.

The v1 rubrics send 191 bytes of preamble that merely restates what the registry
already knows from the path:

```text
# Gap-theme rubric for call_type=discovery, mode=description_only
# Business-provided rubric (themes + descriptions, no annotated example calls).
call_type: discovery
mode: description_only
```

The v2 rubrics written on 2026-08-12 send **1073 bytes**, including a narrative
explaining that the previous version *"fired on 5 of 12 discovery calls and the
entailment verifier rejected 4 of the 5"* and that *"hand review independently
agreed"*. That is a conservatism instruction sitting above every theme.

This is the most likely cause of the spillover measured that day: v2 suppressed
themes whose text was byte-identical to v1 (`Swim Lanes` 4,3 → 0,0;
`Buzzword Fatigue` 3,4 → 1,1) across two independent runs, while v1 reproduced
almost exactly (19/19 and 18/17 total gaps). The effect was originally
attributed to v2's `Do **not** report this theme if…` clause. The header is the
better suspect, and this design's measurement step distinguishes them.

There is no comment mechanism in the format. Anything written in a prompt file
edits the prompt.

### 2. The `.yaml` extension lies

The rubric files are named `.yaml` and open with four lines of YAML-looking
front matter, but nothing ever parses them as YAML — `read_text()` is the only
reader. The rest of each file is prose plus a fenced JSON block. The front
matter is decorative: the real `call_type` and `mode` come from the directory
name and the filename.

### 3. Saving a file changes production

`registry.latest()` returns the highest version label found on disk. Creating
`v2-descriptiononly.yaml` on 2026-08-12 changed what the next batch run would
send, with no config change, no deploy step and no failing test. A draft cannot
be parked in the tree. "Which prompt ran last night" is answerable only by
re-deriving directory listing order.

### 4. Themes have no representation

Every gap-quality decision is per-theme: fire rate, "these two need a rewrite",
the hand labels in `app/services/eval/gap_audit_labels.py`. But a theme exists
only as a `### N. Title` heading inside a text blob.
`theme_falsifiability.load_themes()` recovers them with a regex, and the ad-hoc
analysis scripts written that day repeated both the regex and an em-dash
normalisation to match theme strings against the database.

## Scope

Decided with the user before designing:

| question | decision |
|---|---|
| Who edits prompts | Engineers, via git. Business team sends wording; we commit it. |
| Rubric representation | Flat text, cleaned up. **No** themes-as-data, no rendering from structure. |
| Activation | A checked-in manifest file. |
| Behaviour changes | Mechanics are byte-neutral, **except** the header fix, which is included and measured. |

Explicitly **out of scope** (YAGNI):

- Themes as first-class data with stable ids. Considered and rejected: it would
  make renames non-breaking and per-theme analysis free, but it means the prompt
  is generated rather than authored, and the flat-text path is enough for now.
- Prompts in the database with business-team self-service. Rejected: needs an
  admin surface we don't own (UI is Koushik's team) and an approval path.
- Removing the ~20 lines of duplicated schema boilerplate that appears in all 8
  description-only rubric bodies. This restates a contract the prompts don't own
  — output structure is guaranteed by `response_format` and the Pydantic models
  in `app/domain/response_models.py`, not by prompt text — so it should go. But
  it changes what the model sees in 8 files, and these prompts are demonstrably
  wording-sensitive, so it is a separate change requiring its own measurement.
  (The 6 `fewshot` files are 9-line placeholders and do not contain it.)
- A v3 of the two rewritten themes. The measurement in this design may show the
  exclusion clause was never the problem.

Note that structure would **not** have prevented the spillover: however rubrics
are stored, they reach the model as one text blob. The storage format is not the
cause and restructuring is not the fix.

## Design

### File format

Each prompt file becomes `.md` with optional front matter that is parsed and
**removed** before the content becomes the prompt:

```markdown
---
title: Gap-theme rubric — Discovery (description-only)
owner: business-team
notes: |
  v2 rewrites theme 4 only. v1's title said "pre-call research" while the body
  described in-call surfacing, so it fired on reps who had researched.
---
Role: You are an AI auditor reviewing a New Business call transcript...
```

Rules:

- **Delimited, not `#`-prefixed.** The rubrics use `###` for theme headings, so
  `#`-initial lines are genuine content. A `#` comment convention would be
  ambiguous; `---` fences cannot collide with the prose.
- **The header is optional.** A file without one is entirely body, so the 11
  `.txt` prompts convert by renaming alone.
- **`PromptFile.content` is the body only** — what is sent is what the field
  means.
- **`content_hash` hashes the body, not the file.** Provenance must describe
  what the model saw. This yields a property worth having on its own: two files
  differing only in header hash identically, so editing rationale no longer
  creates a spurious `prompt_versions` row.
- **`kind`, `call_type`, `mode` and `label` keep coming from the path**, never
  from the header, so a header cannot contradict the directory it sits in. This
  is what makes the current `call_type:` / `mode:` lines redundant rather than
  merely misplaced.

Header keys are free-form and unvalidated. Nothing in code reads them; they
exist for humans. Validating them would invent a schema for content whose only
consumer is a reader.

### Activation manifest

`app/prompts/active.toml` names the exact label for every slot. Note the two
distinct meanings of "call type" in this file: a top-level scalar `call_type` is
the *prompt kind* of that name, while the keys inside `[scoring]` and
`[gap_rubric.*]` are call types in the taxonomy sense.

```toml
call_type = "v1"      # the call_type-classification prompt, not a call type
card_type = "v1"
gap_fill  = "v1"

[gap_verification]
dialogue    = "v1"
explanation = "v1"

[scoring]
demo = "v1"
discovery = "v1"
follow_up_demo = "v1"
kickoff = "v1"
pricing_negotiation = "v1"
technical_integration = "v1"

[gap_rubric.descriptiononly]
demo = "v1"
discovery = "v2"
follow_up_demo = "v1"
kickoff = "v1"
pricing_negotiation = "v2"
technical_integration = "v1"

[gap_rubric.fewshot]
demo = "v1"
discovery = "v1"
follow_up_demo = "v1"
kickoff = "v1"
pricing_negotiation = "v1"
technical_integration = "v1"
```

TOML because `tomllib` is in the Python 3.11 standard library — no new
dependency for read-only config. The initial manifest pins exactly the labels
`latest()` resolves to today, so adding it changes nothing by itself.

**Lookup rule.** `active()` resolves the manifest purely from the
`(kind, call_type, mode)` triple it is given, so one rule covers all five kinds
and the table shape never has to be special-cased per kind:

| arguments given | manifest path |
|---|---|
| kind only | top-level scalar `<kind>` |
| kind + mode | `[<kind>]` → key `<mode>` |
| kind + call_type | `[<kind>]` → key `<call_type>` |
| kind + call_type + mode | `[<kind>.<mode>]` → key `<call_type>` |

So `call_type`/`card_type`/`gap_fill` are scalars, `gap_verification` is keyed by
mode, `scoring` by call type, and `gap_rubric` by mode then call type — which is
exactly how the six production slots ask for them today.

### Registry API

- **`active(kind, call_type=None, mode=None) -> PromptFile`** — the
  manifest-pinned file. Replaces `latest()` at all production call sites: the six
  slots in `app/services/batch/run.py::build_step_prompts` and the two in
  `app/api/deps.py`.
- **`latest()` is deleted.** It is the hazard itself. Eval tools needing a
  specific version use the existing `get(label=…)`, which
  `app/services/eval/rubric_version_ab.py` already does. The other eval scripts
  (`harness.py`, `verification_replay.py`, `theme_falsifiability.py`) move to
  `active()`.
- **`get()` and `all()` are unchanged.**
- **The manifest is validated at construction**: every named label must resolve
  to a file, or `PromptManifestError` (a new exception in
  `app/prompts/registry.py`, alongside the existing loader).
  `app/api/deps.py` builds the registry at
  module import, so a typo fails server boot; `batch/run.py` builds it before
  `claim_rows`, so a bad manifest fails the run before it touches the database.
  No new mid-run failure path.
- **Files the manifest does not name are inert.** A draft can sit in the tree
  safely — the thing that was impossible on 2026-08-12.

### Shared theme parser

New `app/prompts/themes.py`:

```python
@dataclass(frozen=True)
class Theme:
    number: int
    title: str
    body: str

def parse_themes(prompt: PromptFile) -> list[Theme]
def theme_key(title: str) -> str
```

It lives beside `registry.py` rather than under `app/services/eval/` because it
encodes knowledge of the file format: if the format changes, the parser must
change with it. Production does not call it — production sends the whole body.
It replaces `theme_falsifiability.load_themes()`, and `theme_key` is the single
normaliser for the em-dash mismatch that was worked around in three places.

## Migration order

1. TDD the registry changes against fixture files in `tmp_path`. No real prompt
   file touched.
2. Add `active.toml` pinning today's resolved labels — a no-op change.
3. Convert all 25 prompt files (11 `.txt`, 14 `.yaml`): rename the extension,
   and for the 14 rubrics move the preamble into a `---` header.
4. Switch call sites: `batch/run.py`, `api/deps.py`, then the four eval scripts.
5. Delete `latest()`. Run the fast suite and the integration suite.
6. Measure the header removal (below).

## Measurement

The manifest earns its keep here. Before step 3, each rubric under test is
copied to a temporary `v0-descriptiononly.md`: a **byte-for-byte copy of the
pre-conversion file with no `---` header added**. Because a headerless file is
entirely body, `v0`'s body — and therefore its `content_hash` — equals the old
prompt exactly, so `v0` reproduces what production sent before the change.
`app/services/eval/rubric_version_ab.py` then compares `v0` against the
converted file, which differs from it only by the stripped preamble.
`active.toml` never names `v0`, so it cannot reach production. `v0` is deleted
after measuring.

Three rubrics, 30 calls, two arms each:

| rubric | header size | why |
|---|---|---|
| discovery | 1073 bytes (v2) | largest expected effect |
| pricing_negotiation | 1073 bytes (v2) | second v2 file |
| demo | 191 bytes (v1) | checks the low-risk case |

Success criteria, fixed in advance because two prompt changes on 2026-08-12 were
evaluated after the fact and one produced a confident wrong conclusion:

- **Header removal is neutral** if total gaps per rubric stay within the churn
  band measured that day — v1 reproduced 19/19 and 18/17, so ≈±2 gaps.
- **If `Swim Lanes` returns to 3–4 firings on Discovery with the header
  stripped**, the header caused the spillover, the `Do not report` clause is
  exonerated, and no v3 is needed.
- **If suppression persists with the header gone**, the clause is the cause and
  v3 goes on the backlog.

One measurement closes both open questions.

## Failure modes

| condition | behaviour |
|---|---|
| manifest names a label with no file | `PromptManifestError` at registry construction |
| manifest missing a slot production needs | `active()` raises, naming the slot |
| unclosed `---` header | load error naming the file |
| no header at all | whole file is body — valid |
| header present but empty | valid |

## Tests

Two are load-bearing and go first:

- Two files differing **only** in header produce the **same** `content_hash` —
  rationale edits must not churn provenance.
- Adding a higher-labelled file does **not** change `active()` — the regression
  guard for the save-goes-live hazard.

Then: header absent from `.content`; a headerless file loads whole; an unclosed
header errors naming the file; a manifest naming a missing label raises at
construction; the shipped manifest covers every slot `build_step_prompts` needs
across all six call types and both rubric modes; `parse_themes` recovers
Discovery's four themes with numbers and titles; `theme_key` matches across an
em-dash/hyphen difference.

Existing `tests/prompts/test_registry.py` (7 tests) is updated: the three
`latest()` tests become `active()` tests.

## Consequences

- Every rubric's `content_hash` changes once, at step 3 (the 11 `.txt` prompts
have no preamble to strip, so only their filenames change and their hashes
stay put). New `prompt_versions`
  rows appear on first use after the change. Existing rows stay valid — they
  describe what those runs actually sent, which is the point of hashing content.
- The 46-call baseline in `analysis` is not touched and stays comparable; only
  future rows reference the new hashes.
- `config.yaml`'s `analyser.gap_rubric_mode` keeps selecting the mode. The
  manifest selects the label within that mode. Mode is a runtime choice; label
  is a content choice.
