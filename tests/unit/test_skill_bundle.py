"""Exact installed Product A skill-bundle provenance contracts."""

from hashlib import sha256

import pytest

from finance_research_agent.application.skill_bundle import (
    compute_skill_version,
    declared_skill_resources,
    validate_logical_path,
)

FILES = {
    "SKILL.md": b"alpha\n",
    "references/workflow-contract.yaml": b"schema_version: 1\n",
}


def test_digest_frames_sorted_raw_paths_and_exact_bytes() -> None:
    expected = "sha256:f5f3262f8c35c9e59ad01373cf25476695a7e303d6e9a501224ecfbfe9f6ce5a"
    assert compute_skill_version(FILES) == expected
    assert compute_skill_version(dict(reversed(tuple(FILES.items())))) == expected
    assert compute_skill_version(tuple(FILES.items())) == expected


def test_digest_changes_for_content_and_logical_path_changes() -> None:
    changed = dict(FILES)
    changed["SKILL.md"] = b"alpha\r\n"
    assert compute_skill_version(changed) != compute_skill_version(FILES)
    changed = {"SKILL.md": FILES["SKILL.md"], "references/changed.yaml": b"schema_version: 1\n"}
    assert compute_skill_version(changed) != compute_skill_version(FILES)


@pytest.mark.parametrize(
    "path",
    (
        "",
        "/SKILL.md",
        "./SKILL.md",
        "references/./file",
        "references/../file",
        "references//file",
        "references/file/",
        "../file",
        "references\\file",
        "C:/file",
        "a\x00b",
    ),
)
def test_paths_reject_unsafe_raw_spelling_before_normalization(path) -> None:
    with pytest.raises(ValueError, match="unsafe logical path"):
        validate_logical_path(path)
    with pytest.raises(ValueError, match="unsafe logical path"):
        compute_skill_version((("SKILL.md", b"skill"), (path, b"resource")))


def test_digest_requires_skill_and_rejects_duplicates_and_non_bytes() -> None:
    with pytest.raises(ValueError, match="SKILL.md is required"):
        compute_skill_version({"references/file": b"resource"})
    with pytest.raises(ValueError, match="duplicate"):
        compute_skill_version((("SKILL.md", b"one"), ("SKILL.md", b"two")))
    with pytest.raises(TypeError, match="bytes"):
        compute_skill_version({"SKILL.md": "not bytes"})


def test_utf8_byte_order_and_framing_are_unambiguous() -> None:
    entries = {"SKILL.md": b"", "references/\u00e9": b"a", "references/z": b"bc"}
    framed = b"".join(
        len(path.encode()).to_bytes(8, "big")
        + path.encode()
        + len(content).to_bytes(8, "big")
        + content
        for path, content in sorted(entries.items(), key=lambda entry: entry[0].encode())
    )
    assert compute_skill_version(entries) == "sha256:" + sha256(framed).hexdigest()


def test_resource_loading_includes_required_and_conditional_resources() -> None:
    skill = (
        b"## Resource Loading\n"
        b"- Conditional: [extra](references/extra.md) \xe2\x80\x94 Load for extra context.\n"
        b"- Required: [workflow](references/workflow-contract.yaml)\n"
        b"## Safety and Forbidden Behavior\n"
        b"- Required: [not a resource](ignored.md)\n"
    )
    assert declared_skill_resources(skill) == (
        "references/extra.md",
        "references/workflow-contract.yaml",
    )


@pytest.mark.parametrize(
    "declarations",
    (
        "- Required: [bad](references/./file)",
        "- Required: [bad](../outside)",
        "- Required: [one](references/file)\n- Conditional: [two](references/file)",
        "- Required: references/file",
    ),
)
def test_resource_loading_rejects_unsafe_duplicate_and_malformed_declarations(declarations) -> None:
    skill = (
        "## Resource Loading\n" + declarations + "\n## Safety and Forbidden Behavior\n"
    ).encode()
    with pytest.raises(ValueError):
        declared_skill_resources(skill)


def test_literal_none_resource_markers_declare_no_resource() -> None:
    assert (
        declared_skill_resources(b"## Resource Loading\n- Required: None.\n- Conditional: None.\n")
        == ()
    )
    assert declared_skill_resources(
        b"## Resource Loading\n"
        b"- Required: [workflow](references/workflow-contract.yaml)\n"
        b"- Conditional: None.\n"
    ) == ("references/workflow-contract.yaml",)


@pytest.mark.parametrize(
    "declarations",
    (
        "- Required: None.\n- Required: None.\n- Conditional: None.",
        "- Required: None.\n- Required: [link](references/file)\n- Conditional: None.",
        "- Required: [link](references/file)\n- Required: None.\n- Conditional: None.",
        "- Required: None.",
        "- Conditional: None.",
        "- Required: None.\n- Conditional: [link](references/file)",
        "- Required: None.\n- Conditional: None.\nUnrecognized resource prose.",
    ),
)
def test_resource_grammar_rejects_none_conflicts_missing_category_and_missing_condition(
    declarations,
):
    skill = (
        "## Resource Loading\n" + declarations + "\n## Safety and Forbidden Behavior\n"
    ).encode()
    with pytest.raises(ValueError):
        declared_skill_resources(skill)
