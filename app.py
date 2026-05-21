"""
app.py — Gradio frontend for the multi-persona design critique panel.

Run:
    export GEMINI_API_KEY=...
    python app.py

Open the URL printed in the terminal (defaults to http://localhost:7860).
"""

import asyncio
import os
from pathlib import Path

import gradio as gr
from google import genai

from design_critique import (
    ALL_CRITICS,
    build_user_content,
    run_critic,
    run_synthesizer,
)


PERSONA_LABELS = {
    "usability": "Usability",
    "accessibility": "Accessibility",
    "information_architecture": "Information Architecture",
    "microcopy": "Microcopy",
    "adversarial": "Adversarial",
    "visual_hierarchy": "Visual Hierarchy",
}

WAITING_SYNTH = "_Waiting for critics to finish…_"


def render_critic_markdown(critique: dict) -> str:
    persona = critique.get("persona", "unknown")
    lines: list[str] = []
    if "error" in critique:
        lines.append(f"**Error:** {critique['error']}")
        if critique.get("raw_response"):
            lines.append("\n<details><summary>Raw response</summary>\n")
            lines.append(f"```\n{critique['raw_response']}\n```")
            lines.append("</details>")
        return "\n".join(lines)

    if critique.get("summary"):
        lines.append(critique["summary"] + "\n")

    findings = critique.get("findings", [])
    if not findings:
        lines.append("_No findings._")
    for f in findings:
        sev = str(f.get("severity", "?")).upper()
        title = f.get("title", "")
        location = f.get("location", "")
        loc_suffix = f" — {location}" if location else ""
        lines.append(f"- **[{sev}] {title}**{loc_suffix}")
        if f.get("problem"):
            lines.append(f"  - {f['problem']}")
        fix = f.get("recommendation") or f.get("rewrite")
        if fix:
            lines.append(f"  - _Fix:_ {fix}")

    needs = critique.get("needs_verification") or []
    if needs:
        lines.append("\n_Needs verification:_")
        for v in needs:
            lines.append(f"- {v}")
    return "\n".join(lines)


def render_synthesis_markdown(synthesis: dict) -> str:
    if "error" in synthesis:
        return f"**Synthesis error:** {synthesis['error']}"

    lines: list[str] = []
    if synthesis.get("overall_assessment"):
        lines.append("### Overall Assessment\n")
        lines.append(synthesis["overall_assessment"] + "\n")

    actions = synthesis.get("ranked_actions", [])
    if actions:
        lines.append("### Ranked Actions\n")
        for a in actions:
            sev = str(a.get("severity", "?")).upper()
            rank = a.get("rank", "?")
            title = a.get("title", "")
            sources = ", ".join(a.get("sources", []))
            lines.append(f"**{rank}. [{sev}] {title}**")
            if sources:
                lines.append(f"_Sources: {sources}_")
            if a.get("issue"):
                lines.append(f"- Issue: {a['issue']}")
            if a.get("action"):
                lines.append(f"- Action: {a['action']}")
            if a.get("rationale"):
                lines.append(f"- Why this rank: {a['rationale']}")
            lines.append("")

    conflicts = synthesis.get("conflicts", [])
    if conflicts:
        lines.append("### Conflicts to Resolve\n")
        for c in conflicts:
            lines.append(f"**{c.get('topic', '')}**")
            for p in c.get("positions", []):
                lines.append(f"- _{p.get('critic', '')}:_ {p.get('position', '')}")
            if c.get("recommendation"):
                lines.append(f"→ {c['recommendation']}")
            lines.append("")

    for section_key, heading in [
        ("structural_observations", "Structural Observations"),
        ("things_done_well", "Things Done Well"),
        ("panel_blind_spots", "Panel Blind Spots"),
    ]:
        items = synthesis.get(section_key) or []
        if items:
            lines.append(f"### {heading}\n")
            for item in items:
                lines.append(f"- {item}")
            lines.append("")

    return "\n".join(lines) if lines else "_No synthesis returned._"


def _panel_outputs(panels: dict, synth: str) -> tuple:
    return (synth, *[panels[p] for p in ALL_CRITICS])


async def critique_stream(
    image_path: str | None,
    extra_files: list | None,
    context: str,
    selected: list[str],
):
    if not image_path:
        raise gr.Error("Upload or paste a design screen first.")
    if not os.environ.get("GEMINI_API_KEY"):
        raise gr.Error("GEMINI_API_KEY environment variable is not set.")
    if not selected:
        raise gr.Error("Select at least one critic.")

    image_paths = [Path(image_path)]
    for f in extra_files or []:
        image_paths.append(Path(f.name if hasattr(f, "name") else f))

    try:
        user_content = build_user_content(image_paths, context or None)
    except ValueError as e:
        raise gr.Error(str(e))

    client = genai.Client()

    panels = {
        p: ("_Skipped_" if p not in selected else "_Running…_")
        for p in ALL_CRITICS
    }
    synth = WAITING_SYNTH
    yield _panel_outputs(panels, synth)

    pending = {
        asyncio.create_task(run_critic(client, p, user_content)): p
        for p in selected
    }
    critiques: list[dict] = []
    while pending:
        done, _ = await asyncio.wait(
            pending.keys(), return_when=asyncio.FIRST_COMPLETED
        )
        for task in done:
            persona = pending.pop(task)
            result = task.result()
            critiques.append(result)
            panels[persona] = render_critic_markdown(result)
            yield _panel_outputs(panels, synth)

    synth = "_Synthesizing…_"
    yield _panel_outputs(panels, synth)

    synthesis = await run_synthesizer(client, critiques, context or None)
    synth = render_synthesis_markdown(synthesis)
    yield _panel_outputs(panels, synth)


def build_ui() -> gr.Blocks:
    with gr.Blocks(title="Design Critique Panel", theme=gr.themes.Soft()) as demo:
        gr.Markdown(
            "# Design Critique Panel\n"
            "Upload or paste a design screen. Six AI critics will review it in "
            "parallel and a synthesizer will rank the top actions."
        )

        with gr.Row():
            with gr.Column(scale=1):
                primary_image = gr.Image(
                    label="Design screen (drag-drop or Cmd+V to paste)",
                    sources=["upload", "clipboard"],
                    type="filepath",
                    height=320,
                )
                extra_files = gr.Files(
                    label="Additional screens (optional)",
                    file_count="multiple",
                    file_types=["image"],
                )
                context_box = gr.Textbox(
                    label="Context",
                    placeholder="e.g. Sign-up flow for citizens, mobile-first",
                    lines=2,
                )
                critic_picker = gr.CheckboxGroup(
                    label="Critics to run",
                    choices=[(PERSONA_LABELS[p], p) for p in ALL_CRITICS],
                    value=list(ALL_CRITICS),
                )
                submit = gr.Button("Critique", variant="primary")

            with gr.Column(scale=2):
                synthesis_md = gr.Markdown(
                    value=WAITING_SYNTH,
                    label="Synthesis",
                    elem_id="synthesis",
                )
                critic_panels: dict[str, gr.Markdown] = {}
                for persona in ALL_CRITICS:
                    with gr.Accordion(PERSONA_LABELS[persona], open=False):
                        critic_panels[persona] = gr.Markdown(
                            value="_Idle._"
                        )

        outputs = [synthesis_md, *[critic_panels[p] for p in ALL_CRITICS]]
        submit.click(
            critique_stream,
            inputs=[primary_image, extra_files, context_box, critic_picker],
            outputs=outputs,
        )

    return demo


if __name__ == "__main__":
    build_ui().launch()
