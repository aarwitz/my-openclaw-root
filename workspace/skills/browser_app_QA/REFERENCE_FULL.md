---
name: browser_app_QA
description: Capture browser UI screenshots for any web app running locally or remotely, verify visible UI changes, and produce reusable evidence for Task Manager issues or chat delivery. Use when you need proof of a browser-rendered change on Linux.
---

# browser_app_QA

Use this skill to verify and capture browser-rendered UI for any browser app, not just LIDI.

## What this skill does

- Runs or targets a web app URL
- Captures screenshots using headless Chromium on Linux
- Produces image evidence for Task Manager issues or chat replies
- Helps validate that a visual change actually renders in a browser

## Current Linux toolchain

Primary capture tools:
- `chromium-browser` (snap package)
- `puppeteer-core` when precise element capture or controlled scrolling is needed

Important snap confinement rule:
- Chromium may fail to write screenshots directly into arbitrary workspace paths.
- Safe pattern:
  1. write screenshot to `/home/aaron/Pictures/browser-qa/`
  2. copy it into the workspace or desired upload path afterward

## Standard screenshot workflow

### 1. Start the app
Use the project's normal local run path.

Example for LIDI:
```bash
cd /home/aaron/repos/lidi-solutions
npm run preview > /tmp/lidi-preview.log 2>&1 &
```

### 2. Wait for local readiness
Check the preview log or curl the local URL.

Example:
```bash
sed -n '1,120p' /tmp/lidi-preview.log
curl -I http://127.0.0.1:8787/
```

### 3A. Fast full-viewport screenshot with headless Chromium
Use this for quick proof when the target UI is already visible in the initial viewport.

```bash
mkdir -p /home/aaron/Pictures/browser-qa
chromium-browser \
  --headless \
  --disable-gpu \
  --no-sandbox \
  --window-size=1440,2400 \
  --hide-scrollbars \
  --screenshot=/home/aaron/Pictures/browser-qa/output.png \
  http://127.0.0.1:8787/
```

Recommended flags:
- `--headless`
- `--disable-gpu`
- `--no-sandbox`
- `--window-size=1440,2400`
- `--hide-scrollbars`

### 3B. Precise section or element screenshot with Puppeteer Core
Use this when the target UI is below the fold, behind interaction, or needs exact element framing.

Reusable script pattern:
```js
node scripts/capture-element-screenshot.mjs \
  http://127.0.0.1:8787/LIDI_index \
  /home/aaron/Pictures/browser-qa/contact.png \
  '#contact' \
  600
```

Expected script behavior:
- open the URL in Chromium through Puppeteer Core
- wait for the selector
- scroll the selector into view
- wait briefly for layout stabilization
- capture the actual element

### 4. Copy screenshot into workspace if needed
```bash
cp /home/aaron/Pictures/browser-qa/output.png /home/aaron/.openclaw/workspace/output.png
```

### 5. Upload evidence
For Task Manager:
```bash
curl -s -X POST "https://tm.lidisolutions.ai/api/issues/<ISSUE_ID>/images?source_type=issue&uploaded_by=Jerry" \
  -F "file=@/home/aaron/.openclaw/workspace/output.png"
```

## When capturing a specific section
Do not rely on a hash anchor alone for proof if the element is below the fold. Headless Chromium may load the route without giving you a trustworthy framed shot of the target section.

Preferred method:
- use Puppeteer Core
- wait for the selector
- scroll it into view
- capture the element itself

Hash anchors are still useful for navigation, but they are not the strongest evidence path by themselves.

Preferred reusable script path in browser-app repos:
- `scripts/capture-element-screenshot.mjs <url> <output-path> [selector] [waitMs]`

## Quality checks

Before calling it done:
- Verify the app is serving the intended branch/build
- Confirm the changed UI is visible in the screenshot
- Confirm the screenshot file exists and is a valid PNG
- If for a Task Manager issue, upload the image and leave a short evidence comment

## Common pitfalls

### Screenshot file not created
If Chromium logs permission errors writing to the workspace:
- write to `/home/aaron/Pictures/browser-qa/` first
- then copy into workspace

### Target section not actually visible in screenshot
If the changed UI is low on the page or hidden behind scroll:
- do not trust a plain viewport screenshot
- switch to Puppeteer Core element capture
- wait for the selector, scroll it into view, then screenshot the element itself

### App serves redirects or SPA fallback
Use the fully qualified page route when needed.

Example:
- `http://127.0.0.1:8787/LIDI_index`

### Local server not ready yet
Wait longer, inspect the preview log, or retry after readiness is confirmed.

## Output expectations

A successful run should yield:
- a concrete screenshot file
- brief verification of what is visible
- optional Task Manager upload/comment
- optional chat delivery with `MEDIA:` attachment line
- when needed, a reusable selector-based capture script committed into the app repo
