---
name: browser_app_QA
description: Deterministic browser UI QA router for screenshot evidence capture and verification on Linux.
---

# Browser App QA Router (Lean + Deterministic)

Use this skill to produce reliable browser-rendered evidence for UI changes.

## Operation Table

| Step | Deterministic Action | Required Output |
|---|---|---|
| App readiness | confirm target URL is serving | reachable URL + status |
| Fast capture | headless Chromium full viewport screenshot | PNG artifact path |
| Precise capture | Puppeteer selector-based element screenshot | section-level PNG artifact |
| Evidence handoff | copy/upload artifact to target workflow | issue/chat evidence link |

## Method Selection

- Use full viewport capture for above-the-fold proof.
- Use selector-based capture for below-fold or exact-component proof.
- If write permissions fail under snap confinement, capture to `/home/aaron/Pictures/browser-qa/` then copy.

## Hard Rules

- No claim without actual screenshot artifact.
- Verify changed UI is visible before reporting success.
- For issue evidence, include artifact link/path and concise verification note.

## Output Contract

Return:
1. target URL
2. capture method used
3. artifact path/link
4. what is visibly verified
5. next action (upload/comment/retest)

## On-Demand Deep Reference

For full command examples, pitfalls, and upload workflows:
- `workspace/skills/browser_app_QA/REFERENCE_FULL.md`
