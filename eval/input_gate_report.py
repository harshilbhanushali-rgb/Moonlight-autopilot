"""Runs the input gate over stored calls and reports, WITHOUT writing anything.

The gate is the one improvement on this backlog that can be validated without
ground truth: its rules are mechanical, so their decisions can be checked by
reading transcripts, and unlike gap generation (43% reproducible) the answer does
not move between runs. This is where that check happens, and
`input_gate.enabled` is only flipped on once it passes.

**Success criterion, fixed before the first run** — exactly four calls are
rejected:

    35f28528  30 words
    b026da73  183 words
    7cf8dcfb  271 words, the platform-switch artifact
    4ac4eea2  the client no-show

`b026da73` is not in `call_type_labels.py::UNCLASSIFIABLE` but is one of the
three sub-300-word calls Part 5 of `problems-and-fixes.md` identifies as having
no conversation in them, so it is a correct rejection. `0bbe93f1` (HelloWork) is
expected to PASS: a real non-rep speaks 1,898 words on the account's own
registered domain, so it is indistinguishable from an ordinary client call and is
accepted as out of scope.

Anything else being rejected means the code disagrees with the phase-0
measurement, not that the corpus changed — find out which is wrong before
changing either.

    uv run python -m eval.input_gate_report
    uv run python -m eval.input_gate_report --out docs/eval/input-gate.json

Scope: `--scope in` (default) restricts to calls still present in
`moonlight_calls`, which needs the client RDS and therefore Tailscale. Use
`--scope all` to report over every stored call without it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import select

from app.core.input_gate_config import InputGateConfig, load_input_gate_config
from app.db.models import CallStorage
from app.db.session import SessionLocal
from app.domain.input_gate import evaluate_input_gate
from app.domain.transcript import Transcript
from eval.call_type_labels import UNCLASSIFIABLE

# Fixed in writing before the first run. See the module docstring.
EXPECTED_REJECTIONS = {
    # "Joveo - quick connect with Steve" — one 30-word turn over 1156s.
    "35f28528-192e-4813-8c5b-56876744ef94",
    # "RTX SonicJobs - Product Enhancements" — 183 words.
    "b026da73-fd2b-4bcd-b76c-1bec0c68c10a",
    # "Feedback on Joveo's new messaging" — 271 words, ends by moving to Zoom.
    "7cf8dcfb-9b84-423d-9204-85041d8cf56f",
    # "Americare <> Joveo || Weekly Call" — client no-show, two reps talking.
    "4ac4eea2-dc3f-431e-96d7-7b1fb0f4622a",
}


def _live_recording_ids() -> set[str] | None:
    """Recording ids still present in moonlight_calls, or None if unreachable.

    A call absent from Koushik's table is out of scope, so the default report
    excludes them — but the client RDS sits on a private address that needs
    Tailscale, and the report is still useful without it.
    """
    try:
        from sqlalchemy import select as client_select

        from app.db.client_models import MoonlightCall
        from app.db.client_session import ClientSessionLocal

        with ClientSessionLocal() as session:
            return set(
                session.execute(client_select(MoonlightCall.avoma_meeting_uuid)).scalars().all()
            )
    except Exception as exc:  # noqa: BLE001 - the report degrades rather than failing
        print(
            f"warning: client RDS unreachable ({type(exc).__name__}); "
            "cannot separate in-scope calls from orphans. Is Tailscale up?",
            file=sys.stderr,
        )
        return None


def _measure(transcript: Transcript, config: InputGateConfig) -> dict:
    verdict = evaluate_input_gate(transcript, config)
    is_rep_by_id = {s.id: s.is_rep for s in transcript.speakers}

    rep_words = non_rep_words = unlinked_words = 0
    for turn in transcript.turns:
        words = len(turn.text.split())
        is_rep = is_rep_by_id.get(turn.speaker_id)
        if is_rep is None:
            unlinked_words += words
        elif is_rep:
            rep_words += words
        else:
            non_rep_words += words

    return {
        "accepted": verdict.accepted,
        "reason": verdict.reason.value if verdict.reason else None,
        "detail": verdict.detail,
        "client_speech_skipped": verdict.client_speech_skipped,
        "total_words": rep_words + non_rep_words + unlinked_words,
        "rep_words": rep_words,
        # The evidence for whether a minimum-client-speech threshold is needed.
        # Deliberately reported and not acted on: inventing the number is what
        # this project's process notes warn against.
        "non_rep_words": non_rep_words,
        "unlinked_words": unlinked_words,
        "turns": len(transcript.turns),
        "speakers": len(transcript.speakers),
    }


def build_report(*, config: InputGateConfig, scope: str) -> dict:
    live = _live_recording_ids() if scope == "in" else None

    with SessionLocal() as session:
        rows = session.execute(
            select(
                CallStorage.avoma_recording_id,
                CallStorage.transcript,
                CallStorage.call_metadata,
            ).order_by(CallStorage.id)
        ).all()

    in_scope, orphans = [], []
    for recording_id, raw, metadata in rows:
        entry = {
            "avoma_recording_id": recording_id,
            "title": (metadata or {}).get("title"),
            "known_bad": UNCLASSIFIABLE.get(recording_id),
            **_measure(Transcript.model_validate(raw), config),
        }
        if live is not None and recording_id not in live:
            orphans.append(entry)
        else:
            in_scope.append(entry)

    rejected = {c["avoma_recording_id"] for c in in_scope + orphans if not c["accepted"]}
    return {
        "config": {
            "min_words": config.min_words,
            "require_client_speech": config.require_client_speech,
        },
        "scope": scope,
        # False when --scope all, or when the client RDS could not be reached.
        # Distinguishes "checked and all 51 are live" from "never checked".
        "scope_checked": live is not None,
        "in_scope": in_scope,
        "orphans": orphans,
        "criterion": {
            "expected": sorted(EXPECTED_REJECTIONS),
            "rejected": sorted(rejected),
            "missing": sorted(EXPECTED_REJECTIONS - rejected),
            "unexpected": sorted(rejected - EXPECTED_REJECTIONS),
            "met": rejected == EXPECTED_REJECTIONS,
        },
    }


def print_report(report: dict) -> None:
    for group in ("in_scope", "orphans"):
        calls = report[group]
        if not calls:
            continue
        if group == "orphans":
            label = "ORPHANS (no longer in moonlight_calls — out of scope)"
        elif report["scope_checked"]:
            label = "IN SCOPE (present in moonlight_calls)"
        else:
            # Either --scope all, or the client RDS was unreachable. Claiming
            # these are in scope would overstate what the report checked.
            label = "ALL STORED CALLS (scope not verified against moonlight_calls)"
        print(f"\n=== {label} — {len(calls)} calls ===")
        print(
            f"{'id':<10}{'words':>7}{'rep':>7}{'client':>8}{'unlnk':>7}"
            f"  {'verdict':<18} title"
        )
        for c in calls:
            verdict = "accepted" if c["accepted"] else c["reason"]
            if c["client_speech_skipped"]:
                verdict += " (R2 abstained)"
            print(
                f"{c['avoma_recording_id'][:8]:<10}{c['total_words']:>7}{c['rep_words']:>7}"
                f"{c['non_rep_words']:>8}{c['unlinked_words']:>7}  {verdict:<18} "
                f"{(c['title'] or '')[:40]}"
            )

    print("\n=== rejected ===")
    for c in report["in_scope"] + report["orphans"]:
        if not c["accepted"]:
            flag = "known-bad" if c["known_bad"] else "not in the labelled bad set"
            print(f"  {c['avoma_recording_id'][:8]}  {c['reason']:<18} [{flag}]  {c['detail']}")

    criterion = report["criterion"]
    print("\n=== criterion ===")
    print(f"  met: {criterion['met']}")
    if criterion["missing"]:
        print(f"  MISSING (expected but accepted): {[i[:8] for i in criterion['missing']]}")
    if criterion["unexpected"]:
        print(f"  UNEXPECTED (false positives):    {[i[:8] for i in criterion['unexpected']]}")

    skipped = [c for c in report["in_scope"] + report["orphans"] if c["client_speech_skipped"]]
    print(f"\n=== R2 abstained on {len(skipped)} call(s) ===")
    for c in skipped:
        print(f"  {c['avoma_recording_id'][:8]}  {c['detail']}")

    accepted = [c for c in report["in_scope"] if c["accepted"]]
    if accepted:
        counts = sorted(c["non_rep_words"] for c in accepted)
        print("\n=== client-side word count across accepted in-scope calls ===")
        print(f"  min {counts[0]}, median {counts[len(counts) // 2]}, max {counts[-1]}")
        thin = [c for c in accepted if c["non_rep_words"] < 200]
        print(f"  under 200 client words: {len(thin)} — {[c['avoma_recording_id'][:8] for c in thin]}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Report-only input gate measurement.")
    parser.add_argument("--out", type=Path, default=None, help="Write the full report as JSON.")
    parser.add_argument(
        "--scope",
        choices=("in", "all"),
        default="in",
        help="'in' restricts to calls still in moonlight_calls (needs Tailscale).",
    )
    parser.add_argument(
        "--min-words",
        type=int,
        default=None,
        help="Override the word floor for this report only. Writes nothing either way.",
    )
    args = parser.parse_args(argv)

    stored = load_input_gate_config()
    # Forced on: the point is to measure what the gate WOULD do while it is
    # still disabled in config.
    config = InputGateConfig(
        enabled=True,
        min_words=args.min_words if args.min_words is not None else stored.min_words,
        require_client_speech=stored.require_client_speech,
    )

    report = build_report(config=config, scope=args.scope)
    print_report(report)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
