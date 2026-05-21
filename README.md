---
title: Design Critique Panel
emoji: 🎨
colorFrom: indigo
colorTo: purple
sdk: gradio
sdk_version: 4.44.0
app_file: app.py
pinned: false
license: mit
---

# Design Critique Panel

A multi-persona AI critique workflow. Sends a design image to a panel of six specialist critics in parallel, then synthesizes their findings into a single ranked action list.

## What's in here

```
DesignCritique/
├── usability.md                  — Nielsen heuristics, cognitive load, error recovery
├── accessibility.md              — WCAG 2.2 AA, screen readers, target sizes
├── information_architecture.md   — labels, grouping, navigation, findability
├── microcopy.md                  — clarity, tone, error messages, button labels
├── adversarial.md                — edge cases, failure modes, breaking the happy path
├── visual_hierarchy.md           — typography, spacing, color, alignment, craft
├── synthesizer.md                — dedupes, resolves conflicts, ranks actions
├── design_critique.py            — CLI workflow (runs panel in parallel)
├── app.py                        — Gradio web UI on top of the CLI
├── requirements.txt
└── README.md
```

Each persona is a separate file so you can tune them independently without touching the workflow code.

## Setup

```bash
pip install -r requirements.txt
export GEMINI_API_KEY=...
```

## Web UI

The fastest way to use this is the Gradio frontend:

```bash
python app.py
```

It opens at `http://localhost:7860`. Drag-drop a screenshot (or paste with Cmd+V), optionally add more screens and context, pick which critics to run, and hit **Critique**. Each critic's panel fills in as it returns; the synthesis lands at the top once they all finish.

## Deploy to Hugging Face Spaces

The repo is already configured as a Space (see the YAML frontmatter at the top of this file). To deploy:

1. **Create a Space.** Go to [huggingface.co/new-space](https://huggingface.co/new-space). Pick a name. Set **SDK** to *Gradio* and **Hardware** to *CPU basic* (free). Visibility: *Public*.
2. **Add the API key as a secret.** In your Space, go to **Settings → Variables and secrets → New secret**. Name it `GEMINI_API_KEY` and paste your key. Secrets are encrypted and never exposed to visitors.
3. **Push the code.** Hugging Face gives you a git URL like `https://huggingface.co/spaces/<you>/<space-name>`. Add it as a remote and push:
   ```bash
   git remote add space https://huggingface.co/spaces/<you>/<space-name>
   git push space main
   ```
   (First push may prompt for an HF access token — generate one at [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) with `write` scope.)
4. **Wait for the build.** The Space rebuilds automatically. You'll see logs in the "Logs" tab. First build takes 3–5 minutes (installing gradio + google-genai). Subsequent pushes are faster.
5. **Use it.** Your Space is live at `https://huggingface.co/spaces/<you>/<space-name>`. Share that URL with anyone.

Quota notes: HF's free CPU tier sleeps the Space after ~48h of inactivity — first visit after a sleep takes ~30s to wake. Gemini quota is on your account; anyone using the public URL consumes it.

## CLI Usage

Single screen:
```bash
python design_critique.py mockup.png
```

With context (recommended — critics give much sharper feedback when they know the goal):
```bash
python design_critique.py mockup.png \
  --context "Sign-up flow for citizens applying for a permit. Mobile-first. Target audience: working adults 25-60, mixed digital literacy."
```

Multiple screens (e.g. before/after, or a flow):
```bash
python design_critique.py before.png after.png \
  --context "Iteration 1 vs Iteration 3 of the invite acceptance screen. We want to understand if the redesign reduces ambiguity around what 'Accept' actually does."
```

Run only a subset of critics:
```bash
python design_critique.py mockup.png --personas usability accessibility microcopy
```

Also output a Markdown report alongside the JSON:
```bash
python design_critique.py mockup.png \
  --output critique.json \
  --markdown critique.md
```

Skip the synthesis step (faster and cheaper, just raw critiques):
```bash
python design_critique.py mockup.png --skip-synthesis
```

## How it works

1. The image is sent to each selected critic in **parallel** (asyncio). Each critic gets only its own system prompt plus the image and your context — they don't see each other's output.
2. Each critic returns structured JSON: a short summary plus a list of severity-ranked findings.
3. The synthesizer (run after the panel completes) receives all critiques and produces a single deduplicated, ranked action list plus conflict callouts and structural observations.

## Design decisions worth knowing

- **Gemini 2.5 Flash** for individual critics (fast, good vision, cheap enough to run six in parallel comfortably).
- **Gemini 2.5 Pro** for the synthesizer (reasoning matters more here than speed).
- **JSON output enforced** via Gemini's `response_mime_type="application/json"` plus a reminder in every persona prompt. Fences are stripped defensively in case the model wraps anyway.
- **Personas stay in their lane** by explicit instruction. This is the single biggest lever for getting useful, non-overlapping critique. If you find critics drifting into each other's territory, tighten the "do NOT comment on..." section in their prompt.
- **Adversarial framing in every prompt** — every persona is told explicitly to be skeptical, not diplomatic. Without this you get sycophantic feedback that doesn't help.

## Tuning the personas

The personas in this repo are calibrated as general-purpose product design critics with a slight gov.sg lean (WCAG 2.2 AA emphasis, IMDA references, plain English defaults). To make them yours:

- **Add domain context to each persona's system prompt.** For DIYGoWhere specifically, you could add: "This product is a no-code site builder for Singapore government agencies. Users are agency staff with mixed technical comfort. The output is a public-facing gov.sg site, so all generated sites must meet IM8 standards." That single addition will sharpen every critique.
- **Upload your design system docs as part of the user content** (not the system prompt) so they show up alongside the image being critiqued. The critic can then call out divergence from your own patterns rather than only generic best practices.
- **Add or remove critics.** A "brand consistency critic" with your design tokens, a "conversion critic" trained on your funnel data, a "WOG Design System adherence critic" — these are all just new `.md` files next to the existing personas. Add them to `ALL_CRITICS` in `design_critique.py`.
- **Adjust severity calibration.** If everyone's calling everything "critical," tighten the severity definitions in each prompt.

## Deployment paths

You have a few options, in order of effort:

1. **Run locally / from CLI.** Fine for solo use. You're done as soon as `pip install` works.
2. **Wrap in a GitHub Action.** You've already automated `shift_bot.yml` with workflow_dispatch — same pattern. Trigger on PR comment, attach the image as artifact, post the synthesis back as a PR comment. About a day of work.
3. **Slack bot.** Listen for image uploads in a `#design-review` channel, run the workflow, post the synthesis back as a threaded reply. Good for team rituals around critique.
4. **Figma plugin.** Plugin exports the current frame as PNG, calls a small backend that runs this workflow, returns the synthesis. Highest value for the team but the most work.

For the GoWhere context, option 2 or 3 is probably the right starting point — fits your existing GitHub Actions muscle and lives where your team already works.

## Cost estimate

Per critique (six critics + synthesizer, one screen, ~200KB image, no context):
- Critics: ~6 × (image tokens + ~800 prompt tokens in + ~1500 tokens out) on Gemini 2.5 Flash
- Synthesizer: ~9000 tokens in + ~2500 tokens out on Gemini 2.5 Pro

Gemini Flash is cheap enough that the panel cost is dominated by the Pro synthesis step. Comfortable for per-iteration use; check current Gemini pricing if you plan to run it in a loop.

## A note on what AI critique is and isn't

This panel is a **first-pass filter**, not a replacement for human review or real user testing. It's strong on:
- Catching the obvious WCAG and heuristic failures you missed because you've been looking at the file for three days
- Surfacing edge cases the happy-path mock doesn't address
- Forcing structured, defensible critique you can take into a design review

It's weak on:
- Anything requiring product knowledge it doesn't have (give it more in `--context`)
- Live interaction quality, motion, perceived performance
- Domain-specific patterns your team has already settled on (feed it your design system)
- Genuine user preference, which it cannot know

Use it as your "pre-flight check" before a real review, not the review itself.
