"""generate() tests with a fake LLM. Quarto render runs for real when available;
otherwise we assert the .qmd was assembled/sanitized and the creator fails gracefully.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest
from open_notebook_creator_sdk import ContentBundle, CreationRequest, ModelRole
from open_notebook_creator_sdk.testing import assert_creator_compliant

from studyguide_creator import StudyGuideCreator

HAS_QUARTO = shutil.which("quarto") is not None


class _FakeLLM:
    def __init__(self, content: str):
        self._content = content

    async def ainvoke(self, _prompt):
        return type("Resp", (), {"content": self._content})()


class _FakeRole(ModelRole):
    def create_language(self, **kwargs):
        if "structured" in kwargs:  # outline call
            return _FakeLLM(
                json.dumps(
                    {
                        "title": "Cell Biology Study Guide",
                        "topics": [
                            {"title": "The Cell Membrane", "summary": "Structure and transport."},
                            {"title": "Mitochondria", "summary": "Energy production."},
                        ],
                    }
                )
            )
        # topic + glossary calls (both non-structured)
        return _FakeLLM(
            "### Key concept\nThe membrane controls transport 🎉.\n\n"
            "### What you need to know\n- Phospholipid bilayer\n\n"
            "### Common traps & misconceptions\n- It is not static\n"
        )


def _request(output_dir: str, formats=None) -> CreationRequest:
    return CreationRequest(
        content=ContentBundle(text="The cell membrane is a phospholipid bilayer.", token_count=8),
        config={"num_topics": 2, "audience": "undergraduate", "formats": formats or ["html"]},
        models={"text": _FakeRole(provider="fake", model="fake")},
        output_dir=output_dir,
        artifact_id="artifact-test-1",
    )


def test_static_compliance():
    assert_creator_compliant(StudyGuideCreator())


@pytest.mark.asyncio
async def test_assembles_qmd_with_topics_and_glossary():
    with tempfile.TemporaryDirectory() as d:
        await StudyGuideCreator().generate(_request(d, ["html"]))
        qmd = (Path(d) / "studyguide.qmd").read_text(encoding="utf-8")
        assert 'title: "Cell Biology Study Guide"' in qmd
        assert "# The Cell Membrane" in qmd and "# Mitochondria" in qmd
        assert "### Key concept" in qmd
        assert "### Common traps & misconceptions" in qmd
        assert "# Glossary" in qmd
        assert "pdf-engine: tectonic" not in qmd  # pdf not requested
        assert "🎉" not in qmd  # sanitized


@pytest.mark.asyncio
async def test_no_text_role_is_failure():
    with tempfile.TemporaryDirectory() as d:
        req = CreationRequest(content=ContentBundle(text="x"), output_dir=d, artifact_id="a")
        result = await StudyGuideCreator().generate(req)
        assert result.status == "FAILURE"
        assert result.errors[0].phase == "setup"


@pytest.mark.skipif(not HAS_QUARTO, reason="quarto not installed")
@pytest.mark.asyncio
async def test_renders_html():
    with tempfile.TemporaryDirectory() as d:
        result = await StudyGuideCreator().generate(_request(d, ["html"]))
        assert result.status == "SUCCESS", (result.user_message, result.errors)
        assert result.schema_id == "studyguide.v1"
        assert result.data["formats"] == ["html"]
        assert len(result.data["topics"]) == 2
        assert (Path(d) / "studyguide.html").stat().st_size > 0
        assert result.files[0].content_type == "text/html"
