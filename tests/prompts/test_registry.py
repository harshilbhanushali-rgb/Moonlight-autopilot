import hashlib

import pytest

from app.prompts.registry import PromptFile, PromptRegistry, load_prompt_file


def write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_load_prompt_file_computes_sha256_content_hash(tmp_path):
    path = write(tmp_path / "scoring" / "v1.txt", "Score this call as High/Medium/Low.")

    prompt_file = load_prompt_file(path, kind="scoring")

    expected_hash = hashlib.sha256(
        "Score this call as High/Medium/Low.".encode("utf-8")
    ).hexdigest()
    assert prompt_file.content_hash == expected_hash
    assert prompt_file.kind == "scoring"
    assert prompt_file.label == "v1"
    assert prompt_file.mode is None
    assert prompt_file.call_type is None


def test_discover_finds_call_type_scoped_scoring_and_flat_card_type_prompts(tmp_path):
    write(tmp_path / "scoring" / "demo" / "v1.txt", "scoring prompt v1")
    write(tmp_path / "card_type" / "v1.txt", "card type prompt v1")

    registry = PromptRegistry(root=tmp_path)

    scoring_files = registry.all(kind="scoring", call_type="demo")
    card_type_files = registry.all(kind="card_type")
    assert [f.label for f in scoring_files] == ["v1"]
    assert scoring_files[0].call_type == "demo"
    assert [f.label for f in card_type_files] == ["v1"]


def test_discover_finds_call_type_scoped_gap_rubric_with_mode(tmp_path):
    write(tmp_path / "gap_rubric" / "demo" / "v1-fewshot.yaml", "fewshot rubric")
    write(tmp_path / "gap_rubric" / "demo" / "v1-descriptiononly.yaml", "description rubric")

    registry = PromptRegistry(root=tmp_path)

    files = registry.all(kind="gap_rubric", call_type="demo")
    modes = {f.mode for f in files}
    assert modes == {"fewshot", "descriptiononly"}
    assert all(f.call_type == "demo" for f in files)


def test_latest_resolves_highest_version_label(tmp_path):
    write(tmp_path / "scoring" / "demo" / "v1.txt", "old")
    write(tmp_path / "scoring" / "demo" / "v2.txt", "new")

    registry = PromptRegistry(root=tmp_path)

    latest = registry.latest(kind="scoring", call_type="demo")
    assert latest.label == "v2"
    assert latest.content == "new"


def test_get_returns_exact_pinned_version(tmp_path):
    write(tmp_path / "scoring" / "demo" / "v1.txt", "old")
    write(tmp_path / "scoring" / "demo" / "v2.txt", "new")

    registry = PromptRegistry(root=tmp_path)

    pinned = registry.get(kind="scoring", label="v1", call_type="demo")
    assert pinned.content == "old"


def test_get_raises_when_version_not_found(tmp_path):
    write(tmp_path / "scoring" / "demo" / "v1.txt", "old")

    registry = PromptRegistry(root=tmp_path)

    with pytest.raises(KeyError):
        registry.get(kind="scoring", label="v99", call_type="demo")


def test_latest_raises_when_no_prompts_for_kind(tmp_path):
    registry = PromptRegistry(root=tmp_path)

    with pytest.raises(KeyError):
        registry.latest(kind="scoring", call_type="demo")


def test_discover_ignores_top_level_pycache_directory(tmp_path):
    write(tmp_path / "scoring" / "demo" / "v1.txt", "scoring prompt v1")
    pycache_dir = tmp_path / "__pycache__"
    pycache_dir.mkdir(parents=True)
    (pycache_dir / "registry.cpython-311.pyc").write_bytes(b"\xa7\x00not utf-8")

    registry = PromptRegistry(root=tmp_path)

    assert [f.label for f in registry.all(kind="scoring", call_type="demo")] == ["v1"]
