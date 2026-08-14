"""The hand labels are the only human ground truth in the project about whether
a gap is correct, so the accessor they are read through is worth pinning."""

from eval.gap_audit_labels import LABELS, lookup


def test_a_label_is_found_by_theme_prefix():
    """Theme strings contain an em dash that does not survive every round trip,
    so lookup matches on prefix rather than equality."""
    assert lookup(274, "Buzzword Fatigue — No Case Study Evidence") is not None
    assert lookup(274, "Buzzword Fatigue - No Case Study Evidence") is not None


def test_an_unreviewed_gap_returns_none_rather_than_a_default():
    """Only 35 of 86 gaps were hand-reviewed. An unreviewed gap must be
    excluded from scoring, not silently counted as good or bad."""
    assert lookup(9999, "Some Theme") is None
    assert lookup(274, "A Theme Nobody Labelled") is None


def test_the_label_set_keeps_both_classes_and_flags_its_borderline_cases():
    good = [v for v in LABELS.values() if v[0]]
    bad = [v for v in LABELS.values() if not v[0]]
    borderline = [v for v in LABELS.values() if v[1]]

    # A one-sided label set could not detect a verifier that drops everything.
    assert len(good) >= 10
    assert len(bad) >= 10
    # Borderline cases exist and are marked so a strict score can exclude them.
    assert 0 < len(borderline) < len(LABELS) // 2


def test_every_label_carries_a_reason():
    """A label without its reasoning cannot be re-checked by anyone else."""
    for key, (_, _, note) in LABELS.items():
        assert note.strip(), key
