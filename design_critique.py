"""
design_critique.py — Multi-persona AI design critique workflow.

Sends a design image (or multiple) to a panel of specialist critic personas
running in parallel, then synthesizes the results into a single ranked action list.

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    python design_critique.py design.png
    python design_critique.py design.png --context "Sign-up flow for citizens"
    python design_critique.py screen1.png screen2.png --context "Before/after redesign"
    python design_critique.py design.png --personas usability accessibility microcopy
    python design_critique.py design.png --skip-synthesis
    python design_critique.py design.png --output critique.json --markdown report.md
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from google import genai
from google.genai import types


PERSONAS_DIR = Path(__file__).parent

# Critics run in parallel. Flash is the right cost/quality tradeoff for
# per-persona critique. Synthesizer uses Pro because synthesis is the step
# that benefits most from stronger reasoning.
CRITIC_MODEL = "gemini-2.5-flash"
SYNTHESIZER_MODEL = "gemini-2.5-pro"

# All available critic personas. Each maps to a file in personas/.
ALL_CRITICS = [
    "usability",
    "accessibility",
    "information_architecture",
    "microcopy",
    "adversarial",
    "visual_hierarchy",
]


def load_persona(name: str) -> str:
    path = PERSONAS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Persona file not found: {path}")
    return path.read_text(encoding="utf-8")


def encode_image(image_path: Path) -> tuple[bytes, str]:
    ext = image_path.suffix.lower().lstrip(".")
    media_type_map = {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "gif": "image/gif",
        "webp": "image/webp",
    }
    media_type = media_type_map.get(ext)
    if not media_type:
        raise ValueError(
            f"Unsupported image type: .{ext}. Use png, jpg, gif, or webp."
        )
    with open(image_path, "rb") as f:
        data = f.read()
    return data, media_type


def build_user_content(image_paths: list[Path], context: str | None) -> list:
    content: list = []
    for path in image_paths:
        data, media_type = encode_image(path)
        content.append(types.Part.from_bytes(data=data, mime_type=media_type))
    instruction_parts = ["Critique this design from your perspective."]
    if len(image_paths) > 1:
        instruction_parts.append(
            f"There are {len(image_paths)} images attached, "
            "in the order shown. Reference them by index when relevant."
        )
    if context:
        instruction_parts.append(f"\nContext from the designer:\n{context}")
    instruction_parts.append(
        "\nReturn your response as valid JSON only. "
        "No preamble. No markdown fences. No commentary outside the JSON."
    )
    content.append("\n".join(instruction_parts))
    return content


def parse_json_response(text: str) -> dict:
    """Best-effort JSON parse. Strips fences if the model added them."""
    text = text.strip()
    if text.startswith("```"):
        # Strip opening fence
        first_newline = text.find("\n")
        text = text[first_newline + 1 :] if first_newline != -1 else text[3:]
        # Strip closing fence
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return json.loads(text.strip())


async def run_critic(
    client: genai.Client,
    persona_name: str,
    user_content: list,
) -> dict:
    system_prompt = load_persona(persona_name)
    raw_text = ""
    try:
        response = await client.aio.models.generate_content(
            model=CRITIC_MODEL,
            contents=user_content,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                max_output_tokens=4096,
            ),
        )
        raw_text = response.text or ""
        result = parse_json_response(raw_text)
        result.setdefault("persona", persona_name)
        return result
    except json.JSONDecodeError as e:
        return {
            "persona": persona_name,
            "error": f"Failed to parse JSON: {e}",
            "raw_response": raw_text,
        }
    except Exception as e:  # noqa: BLE001
        return {"persona": persona_name, "error": str(e)}


async def run_synthesizer(
    client: genai.Client,
    critiques: list[dict],
    context: str | None,
) -> dict:
    system_prompt = load_persona("synthesizer")
    instruction = (
        "Synthesize the following critiques from a panel of design specialists. "
        "Deduplicate overlapping findings, surface genuine conflicts, and "
        "produce a single ranked action list the designer can work from.\n\n"
        f"Designer context: {context or 'None provided'}\n\n"
        "Critiques (JSON):\n"
        f"{json.dumps(critiques, indent=2)}"
    )
    raw_text = ""
    try:
        response = await client.aio.models.generate_content(
            model=SYNTHESIZER_MODEL,
            contents=instruction,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                max_output_tokens=8192,
            ),
        )
        raw_text = response.text or ""
        return parse_json_response(raw_text)
    except json.JSONDecodeError as e:
        return {
            "persona": "synthesizer",
            "error": f"Failed to parse JSON: {e}",
            "raw_response": raw_text,
        }
    except Exception as e:  # noqa: BLE001
        return {"persona": "synthesizer", "error": str(e)}


def render_markdown(result: dict) -> str:
    """Pretty-print the result as a Markdown report."""
    lines: list[str] = []
    lines.append(f"# Design Critique Report")
    lines.append(f"_Generated: {result['timestamp']}_\n")
    if result.get("context"):
        lines.append(f"**Context:** {result['context']}\n")
    lines.append(f"**Images:** {', '.join(result['images'])}\n")

    syn = result.get("synthesis", {})
    if syn and "error" not in syn:
        lines.append("## Overall Assessment\n")
        lines.append(syn.get("overall_assessment", "") + "\n")

        actions = syn.get("ranked_actions", [])
        if actions:
            lines.append("## Ranked Actions\n")
            for a in actions:
                sev = a.get("severity", "?").upper()
                src = ", ".join(a.get("sources", []))
                lines.append(f"### {a.get('rank', '?')}. [{sev}] {a.get('title', '')}")
                lines.append(f"_Sources: {src}_\n")
                lines.append(f"**Issue:** {a.get('issue', '')}\n")
                lines.append(f"**Action:** {a.get('action', '')}\n")
                lines.append(f"**Why this rank:** {a.get('rationale', '')}\n")

        conflicts = syn.get("conflicts", [])
        if conflicts:
            lines.append("## Conflicts to Resolve\n")
            for c in conflicts:
                lines.append(f"**{c.get('topic', '')}**\n")
                for p in c.get("positions", []):
                    lines.append(f"- _{p.get('critic', '')}:_ {p.get('position', '')}")
                lines.append(f"\n→ {c.get('recommendation', '')}\n")

        struct = syn.get("structural_observations", [])
        if struct:
            lines.append("## Structural Observations\n")
            for s in struct:
                lines.append(f"- {s}")
            lines.append("")

        good = syn.get("things_done_well", [])
        if good:
            lines.append("## Things Done Well\n")
            for g in good:
                lines.append(f"- {g}")
            lines.append("")

        blind = syn.get("panel_blind_spots", [])
        if blind:
            lines.append("## Panel Blind Spots\n")
            for b in blind:
                lines.append(f"- {b}")
            lines.append("")

    # Per-critic detail
    lines.append("---\n## Individual Critiques\n")
    for c in result.get("critiques", []):
        name = c.get("persona", "unknown")
        lines.append(f"### {name.replace('_', ' ').title()}\n")
        if "error" in c:
            lines.append(f"_Error: {c['error']}_\n")
            continue
        if c.get("summary"):
            lines.append(f"{c['summary']}\n")
        for f in c.get("findings", []):
            sev = f.get("severity", "?").upper()
            lines.append(f"- **[{sev}] {f.get('title', '')}** — {f.get('location', '')}")
            lines.append(f"  - {f.get('problem', '')}")
            rec = f.get("recommendation") or f.get("rewrite") or ""
            if rec:
                lines.append(f"  - _Fix:_ {rec}")
        if c.get("needs_verification"):
            lines.append(f"\n_Needs verification (testing required):_")
            for v in c["needs_verification"]:
                lines.append(f"- {v}")
        lines.append("")
    return "\n".join(lines)


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a panel of AI design critics in parallel."
    )
    parser.add_argument("images", nargs="+", type=Path, help="Design image(s)")
    parser.add_argument(
        "--context",
        "-c",
        help="Optional context about the design (audience, purpose, constraints)",
    )
    parser.add_argument(
        "--personas",
        nargs="+",
        default=ALL_CRITICS,
        choices=ALL_CRITICS,
        help="Which critic personas to run (default: all)",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        help="Output JSON path (default: critique_<timestamp>.json)",
    )
    parser.add_argument(
        "--markdown",
        "-m",
        type=Path,
        help="Also write a Markdown report to this path",
    )
    parser.add_argument(
        "--skip-synthesis",
        action="store_true",
        help="Skip the synthesis step (faster, cheaper, just raw critiques)",
    )
    args = parser.parse_args()

    if not os.environ.get("GEMINI_API_KEY"):
        sys.exit("Error: GEMINI_API_KEY environment variable not set")

    for img in args.images:
        if not img.exists():
            sys.exit(f"Error: image not found: {img}")

    client = genai.Client()
    user_content = build_user_content(args.images, args.context)

    print(f"→ Running {len(args.personas)} critics in parallel...")
    critiques = await asyncio.gather(
        *[run_critic(client, p, user_content) for p in args.personas]
    )

    # Quick console summary
    for c in critiques:
        name = c.get("persona", "unknown")
        if "error" in c:
            print(f"  ✗ {name}: {c['error']}")
        else:
            n = len(c.get("findings", []))
            print(f"  ✓ {name}: {n} finding{'s' if n != 1 else ''}")

    result = {
        "timestamp": datetime.now().isoformat(),
        "images": [str(p) for p in args.images],
        "context": args.context,
        "model_critic": CRITIC_MODEL,
        "model_synthesizer": SYNTHESIZER_MODEL if not args.skip_synthesis else None,
        "critiques": critiques,
    }

    if not args.skip_synthesis:
        print("→ Synthesizing...")
        result["synthesis"] = await run_synthesizer(client, critiques, args.context)

    timestamp_slug = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = args.output or Path(f"critique_{timestamp_slug}.json")
    output_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"\n✓ JSON written to: {output_path}")

    if args.markdown:
        args.markdown.write_text(render_markdown(result), encoding="utf-8")
        print(f"✓ Markdown written to: {args.markdown}")

    # Print top 5 to terminal
    syn = result.get("synthesis", {})
    actions = syn.get("ranked_actions", []) if syn else []
    if actions:
        print("\n=== TOP PRIORITIES ===")
        for a in actions[:5]:
            sev = a.get("severity", "?").upper()
            print(f"  {a.get('rank', '?')}. [{sev}] {a.get('title', '')}")


if __name__ == "__main__":
    asyncio.run(main())
