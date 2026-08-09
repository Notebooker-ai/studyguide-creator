"""studyguide-creator: an Open Notebook creator that turns notebook content into a
**study guide**, rendered by Quarto to a self-contained HTML page plus a downloadable
PDF (emitted as ``studyguide.v1``).

For each topic the LLM writes a key-concept summary, "what you need to know" bullets,
and common traps/misconceptions; a glossary is generated last and appended. The
sections are assembled into a single ``.qmd`` and handed to the ``quarto`` CLI (PDF
uses the ``tectonic`` engine).
"""

from __future__ import annotations

import asyncio
import json
import re
from importlib import resources
from pathlib import Path
from typing import ClassVar, List, Literal

from ai_prompter import Prompter
from loguru import logger
from open_notebook_creator_sdk import (
    BaseCreator,
    CreationError,
    CreationFile,
    CreationRequest,
    CreationResult,
    CreatorManifest,
    ModelRoleSpec,
)
from open_notebook_creator_sdk.schemas import StudyGuideV1
from pydantic import BaseModel, Field

from .sanitize import sanitize_markdown

__version__ = "0.1.0"

_QMD_NAME = "studyguide.qmd"
_QMD_STEM = "studyguide"

_FORMAT_META: dict[str, tuple[str, str, str]] = {
    "html": ("html", "text/html", "HTML"),
    "pdf": ("pdf", "application/pdf", "PDF"),
}

_RENDER_TIMEOUT_S = 600


class StudyGuideConfig(BaseModel):
    """Per-generation config; its JSON Schema drives the host's generate form."""

    num_topics: int = Field(default=6, ge=1, le=20, description="Number of topics to cover")
    audience: Literal["high_school", "undergraduate", "graduate", "general"] = Field(
        default="undergraduate", description="Reading level / target audience"
    )
    formats: List[Literal["html", "pdf"]] = Field(
        default=["html", "pdf"], description="Output formats to render"
    )


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n", "", text)
        text = re.sub(r"\n```$", "", text)
    return text.strip()


def _render_prompt(name: str, ctx: dict) -> str:
    template = resources.files("studyguide_creator.prompts").joinpath(name).read_text()
    return Prompter(template_text=template).render(ctx)


def _build_front_matter(title: str, formats: List[str]) -> str:
    lines = ["---", f"title: {json.dumps(title)}", "toc: true", "format:"]
    if "html" in formats:
        lines += ["  html:", "    embed-resources: true"]
    if "pdf" in formats:
        lines += [
            "  pdf:",
            "    pdf-engine: tectonic",
            "    geometry:",
            "      - margin=1in",
        ]
    lines.append("---")
    return "\n".join(lines)


async def _quarto_render(output_dir: Path, fmt: str) -> None:
    """Render ``studyguide.qmd`` to one format in-place. Raises on failure."""
    proc = await asyncio.create_subprocess_exec(
        "quarto",
        "render",
        _QMD_NAME,
        "--to",
        fmt,
        cwd=str(output_dir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await asyncio.wait_for(proc.communicate(), timeout=_RENDER_TIMEOUT_S)
    if proc.returncode != 0:
        detail = (err.decode(errors="replace") or out.decode(errors="replace")).strip()
        raise RuntimeError(detail[-2000:] or f"quarto exited {proc.returncode}")


class StudyGuideCreator(BaseCreator):
    config_model: ClassVar[type] = StudyGuideConfig

    @property
    def manifest(self) -> CreatorManifest:
        return self.build_manifest(
            key="studyguides",
            name="Study Guides",
            version=__version__,
            description="LLM-written study guide (key concepts, takeaways, traps, glossary) via Quarto.",
            sdk_compat=">=0.2,<1",
            emits=["studyguide.v1"],
            model_roles=[
                ModelRoleSpec(
                    key="text",
                    kind="language",
                    requires=["structured_json"],
                    description="LLM that writes the study guide.",
                )
            ],
            icon="graduation-cap",
            suggestion_hint=(
                "what to prioritize for review: which key terms, core concepts, and "
                "likely exam themes deserve the most space"
            ),
        )

    async def generate(self, request: CreationRequest) -> CreationResult:
        cfg = StudyGuideConfig.model_validate(request.config)
        role = request.models.get("text")
        if role is None:
            return CreationResult(
                status="FAILURE",
                schema_id="studyguide.v1",
                data={},
                errors=[CreationError(phase="setup", message="missing 'text' model role")],
                user_message="No language model was provided for study guide generation.",
            )

        # 1. Outline (structured JSON): title + topics.
        outline_prompt = _render_prompt(
            "outline.jinja",
            {
                "content": request.content.text,
                "num_topics": cfg.num_topics,
                "audience": cfg.audience,
                "instructions": request.instructions,
            },
        )
        llm = role.create_language(structured={"type": "json"}, max_tokens=2000)
        resp = await llm.ainvoke(outline_prompt)
        raw = resp.content if hasattr(resp, "content") else str(resp)
        try:
            outline = json.loads(_strip_fences(raw))
        except json.JSONDecodeError as e:
            logger.error(f"studyguide: outline was non-JSON: {e}")
            return CreationResult(
                status="FAILURE",
                schema_id="studyguide.v1",
                data={},
                errors=[CreationError(phase="parse", message=f"invalid JSON: {e}", retryable=True)],
                user_message="The model returned an unparseable outline. Please retry.",
            )

        title = (outline.get("title") or "").strip() if isinstance(outline, dict) else ""
        topics_in = outline.get("topics", []) if isinstance(outline, dict) else []
        topics_meta = [
            {"title": (t.get("title") or "").strip(), "summary": (t.get("summary") or "").strip()}
            for t in topics_in
            if isinstance(t, dict) and (t.get("title") or "").strip()
        ]
        if not title or not topics_meta:
            return CreationResult(
                status="FAILURE",
                schema_id="studyguide.v1",
                data={},
                errors=[CreationError(phase="generate", message="no usable outline produced")],
                user_message="No study guide outline could be generated from this content.",
            )

        # 2. One section per topic (key concept + need-to-know + traps).
        topic_llm = role.create_language(max_tokens=2500)
        bodies: list[str] = []
        for i, tp in enumerate(topics_meta, start=1):
            prompt = _render_prompt(
                "topic.jinja",
                {
                    "content": request.content.text,
                    "guide_title": title,
                    "audience": cfg.audience,
                    "instructions": request.instructions,
                    "topic_number": i,
                    "total_topics": len(topics_meta),
                    "topic_title": tp["title"],
                    "topic_summary": tp["summary"],
                },
            )
            tresp = await topic_llm.ainvoke(prompt)
            body = tresp.content if hasattr(tresp, "content") else str(tresp)
            bodies.append(sanitize_markdown(_strip_fences(body)))

        # 3. Glossary (best-effort; appended at the bottom).
        glossary_md = ""
        try:
            gprompt = _render_prompt(
                "glossary.jinja",
                {
                    "content": request.content.text,
                    "topics": topics_meta,
                    "instructions": request.instructions,
                },
            )
            gresp = await topic_llm.ainvoke(gprompt)
            gbody = gresp.content if hasattr(gresp, "content") else str(gresp)
            glossary_md = sanitize_markdown(_strip_fences(gbody)).strip()
        except Exception as e:  # noqa: BLE001 - glossary is optional
            logger.warning(f"studyguide: glossary generation failed: {e}")

        # 4. Assemble the single-document .qmd.
        output_dir = Path(request.output_dir)
        parts = [_build_front_matter(title, cfg.formats), ""]
        for tp, body in zip(topics_meta, bodies):
            parts.append(f"\n# {tp['title']}\n")
            parts.append(body)
            parts.append("")
        if glossary_md:
            parts.append("\n# Glossary\n")
            parts.append(glossary_md)
            parts.append("")
        (output_dir / _QMD_NAME).write_text("\n".join(parts), encoding="utf-8")

        # 5. Render each requested format (best-effort: one failure -> PARTIAL).
        files: list[CreationFile] = []
        warnings: list[str] = []
        errors: list[CreationError] = []
        rendered: list[str] = []
        for fmt in cfg.formats:
            ext, content_type, label = _FORMAT_META[fmt]
            out_name = f"{_QMD_STEM}.{ext}"
            try:
                await _quarto_render(output_dir, fmt)
                if not (output_dir / out_name).exists():
                    raise RuntimeError("quarto reported success but produced no output file")
                files.append(
                    CreationFile(filename=out_name, content_type=content_type, path=out_name, label=label)
                )
                rendered.append(fmt)
            except FileNotFoundError:
                logger.error("studyguide: 'quarto' binary not found on PATH")
                errors.append(CreationError(phase="render", message="quarto not installed"))
                warnings.append("Quarto is not installed on the server; cannot render the study guide.")
                break
            except Exception as e:  # noqa: BLE001 - one format failing is non-fatal
                logger.warning(f"studyguide: {fmt} render failed: {e}")
                warnings.append(f"{label} export failed.")
                errors.append(CreationError(phase="render", message=f"{fmt}: {e}"))

        if not rendered:
            return CreationResult(
                status="FAILURE",
                schema_id="studyguide.v1",
                data={},
                warnings=warnings,
                errors=errors or [CreationError(phase="render", message="no formats rendered")],
                user_message="The study guide could not be rendered to any format.",
            )

        files.sort(key=lambda f: 0 if f.content_type == "text/html" else 1)

        data = StudyGuideV1(
            title=title,
            topics=[{"title": t["title"], "summary": t["summary"] or None} for t in topics_meta],
            formats=rendered,
        ).model_dump()

        return CreationResult(
            status="PARTIAL" if errors else "SUCCESS",
            schema_id="studyguide.v1",
            data=data,
            files=files,
            warnings=warnings,
            errors=errors,
        )
