"""Post-generation editing: generate() attaches the .qmd as a ``source`` file and
render() re-runs Quarto on an edited .qmd without touching the LLM. ``_quarto_render``
is monkeypatched to a fake that writes the expected output file so the tests do not
depend on the ``quarto`` binary.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from open_notebook_creator_sdk import RenderRequest, SourceDoc
from test_studyguide import _request

import studyguide_creator
from studyguide_creator import StudyGuideCreator

EDITED_QMD = (
    '---\ntitle: "Edited Guide"\nformat:\n  html:\n    embed-resources: true\n---\n\n'
    "# The Cell Membrane\n\nHand-edited topic.\n"
)


def _fake_quarto(rendered: list[str], fail: set[str] | None = None):
    """Fake ``_quarto_render``: records calls and writes ``studyguide.<fmt>``."""

    async def fake(output_dir: Path, fmt: str) -> None:
        assert (output_dir / "studyguide.qmd").exists()
        rendered.append(fmt)
        if fail and fmt in fail:
            raise RuntimeError(f"boom {fmt}")
        (output_dir / f"studyguide.{fmt}").write_text(f"<{fmt}>", encoding="utf-8")

    return fake


def _render_request(output_dir: str, sources: list[SourceDoc], formats: list[str]) -> RenderRequest:
    return RenderRequest(
        sources=sources,
        config={"num_topics": 2, "audience": "undergraduate", "formats": formats},
        data={
            "title": "Cell Biology Study Guide",
            "topics": [{"title": "The Cell Membrane", "summary": "Structure and transport."}],
            "formats": ["html"],
        },
        output_dir=output_dir,
        artifact_id="artifact-test-1",
    )


def test_manifest_declares_editable_source():
    assert StudyGuideCreator().manifest.editable_source is True


@pytest.mark.asyncio
async def test_generate_attaches_qmd_as_source(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(studyguide_creator, "_quarto_render", _fake_quarto(calls))
    with tempfile.TemporaryDirectory() as d:
        result = await StudyGuideCreator().generate(_request(d, ["pdf", "html"]))
        assert result.status == "SUCCESS", (result.user_message, result.errors)
        sources = [f for f in result.files if f.role == "source"]
        assert len(sources) == 1
        src = sources[0]
        assert src.filename == "studyguide.qmd" and src.path == "studyguide.qmd"
        assert src.content_type == "text/markdown"
        assert (Path(d) / src.path).exists()
        outputs = [f for f in result.files if f.role == "output"]
        # Outputs come first, HTML first among them; the source trails.
        assert [f.filename for f in outputs] == ["studyguide.html", "studyguide.pdf"]
        assert result.files[0].content_type == "text/html"
        assert result.files[-1].role == "source"


@pytest.mark.asyncio
async def test_render_rerenders_edited_qmd(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(studyguide_creator, "_quarto_render", _fake_quarto(calls))
    with tempfile.TemporaryDirectory() as d:
        req = _render_request(
            d,
            [SourceDoc(filename="studyguide.qmd", content_type="text/markdown", content=EDITED_QMD)],
            ["html", "pdf"],
        )
        result = await StudyGuideCreator().render(req)
        assert result.status == "SUCCESS", (result.user_message, result.errors)
        assert result.schema_id == "studyguide.v1"
        assert calls == ["html", "pdf"]
        assert (Path(d) / "studyguide.qmd").read_text(encoding="utf-8") == EDITED_QMD
        outputs = [f for f in result.files if f.role == "output"]
        assert [f.filename for f in outputs] == ["studyguide.html", "studyguide.pdf"]
        assert result.files[0].content_type == "text/html"
        assert [f.filename for f in result.files if f.role == "source"] == ["studyguide.qmd"]
        # data: formats reflect this render; everything else carried forward.
        assert result.data["formats"] == ["html", "pdf"]
        assert result.data["title"] == "Cell Biology Study Guide"
        assert result.data["topics"][0]["title"] == "The Cell Membrane"


@pytest.mark.asyncio
async def test_render_partial_when_one_format_fails(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(studyguide_creator, "_quarto_render", _fake_quarto(calls, fail={"pdf"}))
    with tempfile.TemporaryDirectory() as d:
        req = _render_request(
            d,
            [SourceDoc(filename="studyguide.qmd", content_type="text/markdown", content=EDITED_QMD)],
            ["html", "pdf"],
        )
        result = await StudyGuideCreator().render(req)
        assert result.status == "PARTIAL"
        assert result.data["formats"] == ["html"]
        assert any(e.phase == "render" and "pdf" in e.message for e in result.errors)
        assert [f.filename for f in result.files if f.role == "output"] == ["studyguide.html"]
        assert [f.filename for f in result.files if f.role == "source"] == ["studyguide.qmd"]


@pytest.mark.asyncio
async def test_render_without_qmd_source_is_failure(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(studyguide_creator, "_quarto_render", _fake_quarto(calls))
    with tempfile.TemporaryDirectory() as d:
        req = _render_request(
            d,
            [SourceDoc(filename="other.md", content_type="text/markdown", content="# nope")],
            ["html"],
        )
        result = await StudyGuideCreator().render(req)
        assert result.status == "FAILURE"
        assert result.errors[0].phase == "render"
        assert "studyguide.qmd" in result.errors[0].message
        assert calls == []


@pytest.mark.asyncio
async def test_render_when_quarto_missing_is_failure(monkeypatch):
    async def missing(_output_dir: Path, _fmt: str) -> None:
        raise FileNotFoundError("quarto")

    monkeypatch.setattr(studyguide_creator, "_quarto_render", missing)
    with tempfile.TemporaryDirectory() as d:
        req = _render_request(
            d,
            [SourceDoc(filename="studyguide.qmd", content_type="text/markdown", content=EDITED_QMD)],
            ["html", "pdf"],
        )
        result = await StudyGuideCreator().render(req)
        assert result.status == "FAILURE"
        assert result.errors[0].message == "quarto not installed"
