---
name: task-manager
description: Manage the RSL Task Manager at http://127.0.0.1:8000 for story/sprint visibility and Dwight-routed execution. Task Manager runtime and source are owned by Dwight in /home/aaron/.openclaw/workspaces/dwight/rsl-task-manager. Non-Dwight agents are view-only unless explicitly delegated by Dwight. Do not use for Task Manager source-code changes; use the task-manager-maintainer skill for backend/frontend TM development. IMPORTANT — before creating any story, apply the 5 quality gates (EWAG value chain, executable, no duplicate, pilot-ready, material). Read ELITE_PROJECT_BRIEF.md for EWAG business context.
metadata: {"clawdbot":{"emoji":"📋"}}
---

# RSL Task Manager

The Task Manager is RSL's project management system running on the same Linux host as Jerry.

Host mapping for Jerry runtime:
- `http://127.0.0.1:8000` = local Task Manager on the RSL machine (preferred for local commands/scripts)
- `http://localhost:8000` = same local service
- `http://rsl:8000` = host alias of the same service (do not use in curl commands; prefer `127.0.0.1`)

Dwight is the official Task Manager orchestrator/developer. Jerry uses this skill for coordination support, cross-agent visibility, and evidence logging when needed.

Source code: `/home/aaron/.openclaw/workspaces/dwight/rsl-task-manager/` (FastAPI + SQLite + vanilla JS frontend).

## ⚠️ Story Creation Rules — READ FIRST

**Jerry creates too many stories.** Follow these hard rules to prevent busywork:

### Before creating ANY story, pass ALL five gates:

1. **EWAG value chain test**: Does this directly serve resident engagement → measurable data → owner ROI proof? If the story doesn't trace back to EWAG's core business (turning empty gyms into leasing advantages), do NOT create it.

2. **Executable test**: Can the assignee sit down and complete a concrete deliverable (code change, design, config, doc update) within one sprint? If it's vague ("investigate," "explore," "improve cohesion," "make more consistent"), rewrite it as a specific action or skip it. **Exception**: investigation stories ARE allowed when they are a blocker to an important executable action — in that case, the story title must state what it unblocks (e.g., "[Backend] Investigate booking API response format — blocks: wire booking flow to CoachingView").

3. **Deduplication test**: Search existing issues (`/api/issues/search?q=<keywords>&search_in=all`) BEFORE creating. If a similar story exists — even with different wording — update the existing one instead. Never create a duplicate.

4. **ROI test**: Is this among the highest-impact actions Jerry could take right now? Estimate the effort (small/medium/large) and the impact on EWAG's ability to close deals, retain residents, or prove ROI. Prioritize high-impact/low-effort. Don't create low-impact stories when higher-impact work exists.

5. **Materiality test**: Is this actually important? Not every screenshot imperfection needs a story. Not every idea deserves a ticket. Ask: "Would Aaron care about this?" If the answer is "probably not," don't create it.

### Stories Jerry should NOT create:
- "Investigate [vague topic]" with no linked blocker — too vague, not executable
- "Improve [X] consistency" — rewrite as specific UI change or skip
- "Research [technology/approach]" as standalone — research is part of doing, not a separate story (unless it's a research spike that blocks a concrete next action — then link it)
- "Make [screen] more cohesive" — what does that mean concretely? Be specific or skip
- Duplicate/overlapping stories that cover ground already tracked
- Polish stories for screens that aren't functionally complete yet
- Stories for features not in EWAG's actual service offering
- Low-ROI busywork when higher-impact work exists in the backlog

### Stories Jerry SHOULD create:
- Specific bug fixes with clear repro steps
- Concrete UI changes: "[Coaching] Add coach photo to booking confirmation card"
- Backend wiring: "[Backend] Wire booking endpoint to CoachingView session list"
- Owner dashboard metrics: "[Owner] Add weekly active residents chart to utilization tab"
- Test coverage: "[Testing] Add UI test for rewards tier progression flow"
- Content fixes: "[Home] Replace placeholder text with realistic resident data"
- Investigation spikes that unblock important work: "[Backend] Investigate booking API response format — blocks: wire booking flow to CoachingView"
- Product ideas backed by research: "[Product] Add coach availability indicator to Home — competitor analysis shows 40% higher booking rates"

### What EWAG actually delivers (know this before writing stories)

EWAG sells **human-led wellness activation** for apartment buildings:
- On-site personal trainers (5am–8pm), 1-on-1 coaching, group fitness
- Nutrition coaching & wellness events
- Resident rewards driving engagement habits
- ResiLife owner dashboard proving utilization/engagement/retention/NOI
- Zero CapEx, zero buildouts — EWAG manages everything

**Key metrics EWAG sells on**: $130–$400+ rent premium/unit/month, 20–40% faster lease-up, 3× resident satisfaction, 68% utilization growth, 87% participation rate. Every story should serve these outcomes.

**Target customers**: Class A/B multifamily, 150+ units, existing fitness amenity, property managers / asset managers / REIT decision-makers.

The app exists to make EWAG's human wellness service feel like a **private fitness club** to residents and provide **real-time ROI proof** to owners.

## API Reference

Canonical local base URL for Jerry: `http://127.0.0.1:8000`

Compatibility aliases: `http://localhost:8000`, `http://rsl:8000`

Rule: In commands and scripts, always use `http://127.0.0.1:8000` to avoid environment-dependent hostname resolution.

### Issues (Stories/Tasks)

**Create issue:**
```bash
curl -s -X POST http://127.0.0.1:8000/api/issues \
  -H "Content-Type: application/json" \
  -d '{
    "title": "[Coaching] Fix button text visibility",
    "description": "The coaching view has a button where text cannot be seen against the background. Fix the contrast.\n\nAcceptance criteria:\n- Button text readable on all themes\n- Screenshot evidence attached",
    "created_by": "Jerry",
    "assigned_to": "Jerry",
    "branch": "issue-48-coaching-button-contrast"
  }'
```

**Get issue:**
```bash
curl -s http://127.0.0.1:8000/api/issues/43
```

**Update issue (status, assignment, title, description, branch):**
```bash
curl -s -X PATCH http://127.0.0.1:8000/api/issues/43 \
  -H "Content-Type: application/json" \
  -d '{"status": "done", "assigned_to": "Taylor"}'
```

**Set branch on existing issue:**
```bash
curl -s -X PATCH http://127.0.0.1:8000/api/issues/43 \
  -H "Content-Type: application/json" \
  -d '{"branch": "issue-43-coaching-button-fix"}'
```
Valid statuses: `to_do`, `in_progress`, `in_review`, `done`

**List issues (with filters):**
```bash
# All issues in active sprint
curl -s "http://127.0.0.1:8000/api/issues?sprint_id=2"

# Backlog only
curl -s "http://127.0.0.1:8000/api/issues?in_backlog=true"
```

**Search issues:**
```bash
# By text
curl -s "http://127.0.0.1:8000/api/issues/search?q=rewards&search_in=all"

# By ID
curl -s "http://127.0.0.1:8000/api/issues/search?q=%2343&search_in=all"

# With filters
curl -s "http://127.0.0.1:8000/api/issues/search?q=coaching&assigned_to=Jerry&status=in_progress"
```

**Assign issue to sprint:**
```bash
curl -s -X POST "http://127.0.0.1:8000/api/issues/43/assign-to-sprint?sprint_id=2"
```

### Comments

**Add comment to issue:**
```bash
curl -s -X POST http://127.0.0.1:8000/api/issues/43/comments \
  -H "Content-Type: application/json" \
  -d '{
    "content": "Fixed button contrast. Screenshot attached. Build passes, all 5 tab tests green.",
    "username": "Jerry"
  }'
```

### Image Uploads

**Upload image attached to the issue (default issue-level evidence):**
```bash
curl -s -X POST "http://127.0.0.1:8000/api/issues/43/images?source_type=issue&uploaded_by=Jerry" \
  -F "file=@/home/aaron/.openclaw/media/inbound/rewards-screenshot.png"
```

**Upload image attached to issue description:**
```bash
curl -s -X POST "http://127.0.0.1:8000/api/issues/43/images?source_type=description&uploaded_by=Jerry" \
  -F "file=@/home/aaron/.openclaw/media/inbound/description-context.png"
```

**Upload image attached to a specific comment:**
```bash
curl -s -X POST "http://127.0.0.1:8000/api/issues/43/images?source_type=comment&comment_id=118&uploaded_by=Jerry" \
  -F "file=@/home/aaron/.openclaw/media/inbound/comment-evidence.png"
```

Upload query params:
- `source_type`: `issue` | `description` | `comment`
- `comment_id`: required when `source_type=comment`
- `uploaded_by`: optional username

Image response includes:
- `id`, `filename`, `url`
- `issue_id`, `comment_id`, `source_type`
- `uploaded_by`, `uploaded_at`

Allowed types: .jpg, .jpeg, .png, .gif, .webp

Validation notes:
- Server enforces allowed image extensions.
- For `source_type=comment`, the API validates that the comment belongs to the target issue.

Issue payload notes:
- Issue detail payloads include comment images nested under each comment for direct rendering in issue UI.

**Delete image:**
```bash
curl -s -X DELETE http://127.0.0.1:8000/api/issues/43/images/9
```

### Sprints

**Create sprint:**
```bash
curl -s -X POST http://127.0.0.1:8000/api/sprints \
  -H "Content-Type: application/json" \
  -d '{"name": "ResiLife Sprint 3 — Coaching Polish"}'
```

**List sprints:**
```bash
curl -s http://127.0.0.1:8000/api/sprints
```

**Get active sprint:**
```bash
curl -s http://127.0.0.1:8000/api/sprints/active
```

**Start sprint (deactivates all others):**
```bash
curl -s -X POST http://127.0.0.1:8000/api/sprints/3/start
```

**End sprint (moves issues to backlog):**
```bash
curl -s -X POST http://127.0.0.1:8000/api/sprints/2/end
```

### Users

**Login/create user:**
```bash
curl -s -X POST http://127.0.0.1:8000/api/users/login \
  -H "Content-Type: application/json" \
  -d '{"username": "Jerry"}'
```

**List users:**
```bash
curl -s http://127.0.0.1:8000/api/users
```

## Story Writing Standards

**Before writing a story, verify it passes all 5 gates above.** Then follow these conventions:

### Branch Discipline

Every story that involves code work MUST have a linked branch:

1. **Branch naming**: `issue-<id>-<short-slug>` (e.g. `issue-47-coaching-booking-fix`)
2. **When creating a story**: include the `branch` field in the create payload. If the issue ID isn't known yet (auto-assigned), create the issue first, read the returned ID, then immediately PATCH the branch field.
3. **Before resuming any existing story or creating any new branch**: run `/home/aaron/.openclaw/scripts/reconcile-task-manager-with-git.py --apply`. If the linked branch is already merged to `main`, the issue must be moved to `done` and work must not restart on a replacement branch.
4. **When starting work on an existing story**: if `branch` is null, create the branch and PATCH it onto the issue before writing code.
5. **The issue number is always the branch prefix** — this is how we trace branches back to stories.
6. **Short slug**: lowercase, hyphen-separated, 3-5 words max describing the change.

### Git ↔ Task Manager Reconciliation

This is a hard rule for Jerry:

1. Git is the source of truth for whether a linked implementation branch has already landed on `main`.
2. Task Manager must mirror that reality immediately.
3. Before touching any `in_progress` or `in_review` issue with a linked branch, run:
  ```bash
  /home/aaron/.openclaw/scripts/reconcile-task-manager-with-git.py --apply
  ```
4. If the branch is already merged to `main`, mark the issue `done` instead of reopening it.
5. Never create a second branch for an issue whose linked branch already landed on `main`.
6. If follow-up work is needed after merge, create a new issue with a new branch; do not recycle the completed issue.

Example workflow:
```bash
# Create story
ISSUE=$(curl -s -X POST http://127.0.0.1:8000/api/issues \
  -H "Content-Type: application/json" \
  -d '{"title": "[Coaching] Wire booking endpoint", "description": "...", "created_by": "Jerry", "assigned_to": "Jerry"}' | jq -r '.id')

# Set branch
BRANCH="issue-${ISSUE}-coaching-booking-wire"
curl -s -X PATCH "http://127.0.0.1:8000/api/issues/${ISSUE}" \
  -H "Content-Type: application/json" \
  -d "{\"branch\": \"${BRANCH}\"}"

# Create the git branch
cd /home/aaron/repos/EWAG-dev-iosApp && git checkout -b "$BRANCH"
```

**Title format:** `[Area] Short action description`
- Areas: `Coaching`, `Nutrition`, `Community`, `Rewards`, `Connector`, `Home`, `Profile`, `Auth`, `Backend`, `Infra`, `Testing`, `Owner`
- Title must describe a **concrete deliverable**, not a vague goal

**Description format:**
```
<What needs to change, why it matters to EWAG's value chain, and what "done" looks like>

Acceptance criteria:
- [ ] Specific, testable condition 1
- [ ] Specific, testable condition 2
- [ ] Screenshot/video evidence if UI change
```

**Quality checklist before submitting:**
- [ ] Searched existing issues — no duplicate exists
- [ ] Title is a concrete action, not an investigation (unless it's a blocker — see gate 2)
- [ ] Description explains WHY this matters to EWAG/ResiLife
- [ ] Acceptance criteria are binary pass/fail testable
- [ ] If investigation story, it explicitly states what important action it unblocks
- [ ] ROI estimate: this is among the highest-impact things Jerry could do right now
- [ ] Assignee can complete this in one sprint

**Assignment guidelines:**
- `Jerry` — Code changes, tests, infrastructure, automation, UI fixes, design implementation
- `Aaron` — Product decisions, stakeholder alignment, client communication, final visual approval
- `Taylor` — Feature design input, marketing copy, user flow decisions

**Status workflow:**
1. `to_do` — Created, not started
2. `in_progress` — Actively being worked on
3. `in_review` — PR open or awaiting visual/product review
4. `done` — Merged, tested, and verified

## Progress Update Pattern

When making progress on a story, Jerry should:

1. **Move to in_progress** when starting work
2. **Run the reconciler first** if the issue already has a branch, so merged work is closed before any new coding starts
3. **Check recent comments first** (last 3); only post if there is materially new information
4. **Post comment** in dense format (3 bullets max: changed, evidence, next)
5. **Upload screenshot** if the change is visual — capture via ewag-visual-qa skill, then upload:
   ```bash
   # After capturing screenshot to /home/aaron/.openclaw/media/inbound/
   # Issue-level image:
   curl -s -X POST "http://127.0.0.1:8000/api/issues/43/images?source_type=issue&uploaded_by=Jerry" \
     -F "file=@/home/aaron/.openclaw/media/inbound/coaching-screenshot.png"

   # If tied to a specific comment, pass source_type=comment and comment_id:
   curl -s -X POST "http://127.0.0.1:8000/api/issues/43/images?source_type=comment&comment_id=118&uploaded_by=Jerry" \
     -F "file=@/home/aaron/.openclaw/media/inbound/coaching-screenshot.png"
   ```
6. **Move to in_review** when PR is ready
7. **Move to done** when merged and verified, or immediately when the reconciler detects the linked branch already landed on `main`

Avoid comments that only restate prior status with no new output.

## Editing Task Manager Source Code

For making code changes to the Task Manager itself (adding fields, changing endpoints, updating frontend modals), load the `task-manager-maintainer` skill. It covers backend/frontend sync, additive migrations, validation checklists, and the required workflow for safe end-to-end changes.

Source code: `/home/aaron/.openclaw/workspaces/dwight/rsl-task-manager/` (FastAPI + SQLAlchemy + SQLite + vanilla JS)
Database: `/home/aaron/.openclaw/workspaces/dwight/taskmanager.db`

**To restart after code changes:**
```bash
pkill -f "uvicorn main:app" || true
cd ~/repos/Task-Manager && nohup bash start.sh > /tmp/task-manager.log 2>&1 &
```

## Autonomous Product Development Loop

Jerry doesn't just execute assigned tasks — Jerry runs a continuous product development cycle. The Task Manager is the central hub for this loop. Every action Jerry takes should feed back into the system.

### The Loop: Ideate → Research → Prioritize → Execute → Test → Review → Learn → Repeat

**Phase 1: Ideate**
- After every build review, every screenshot comparison, every client email — ask: "What would make this product more valuable to EWAG's customers?"
- Compare current app state against EWAG's website messaging and value props (see `ELITE_PROJECT_BRIEF.md`)
- Compare current screenshots against previous versions saved in Google Drive to spot regressions and improvements
- Ideas must be grounded in EWAG's actual business: resident engagement, owner analytics, trainer operations

**Phase 2: Research & Validate**
- Before creating a story for a product idea, do lightweight research:
  - Use `web_search` to check competitor apps (Mindbody, ClassPass, building management apps)
  - Look at what similar wellness/fitness platforms charge, what features they highlight
  - Estimate potential impact: "If we add X, does it help EWAG close more deals or retain more residents?"
- Spawn a subagent for deeper research when needed (competitive landscape, market sizing, best practices)
- Document research findings in the story description so the rationale is preserved

**Phase 3: Prioritize by ROI**
- Before adding any new story to the sprint, rank it against existing backlog items by estimated ROI:
  - **ROI = Impact on EWAG's deal-closing ability ÷ Engineering effort**
  - High impact + low effort = do first (quick wins)
  - High impact + high effort = plan carefully, break down
  - Low impact + any effort = backlog or skip
- The sprint should always contain the highest-ROI items available, not just the newest ideas
- Re-rank the backlog regularly — an idea that was low-priority last week might be high-priority after new client feedback

**Phase 4: Execute**
- Pick the highest-ROI story, move to in_progress, write code, build, test
- Keep the git workflow clean: feature branch → PR → review → merge
- Leave dense, meaningful comments on the story (what changed, evidence, next step)

**Phase 5: Test & Review**
- Build on Mac node, capture screenshots, compare against previous Drive uploads
- Run automated tests, observe results
- Ask: "Does this move the needle for EWAG? Would a property owner be impressed seeing this?"

**Phase 6: Learn & Feed Back**
- After completing a story, write lessons learned to `memory/YYYY-MM-DD.md`
- Update `MEMORY.md` with durable insights (patterns that work, patterns that don't)
- If the completed work reveals new opportunities or problems, feed them back to Phase 1
- If a product idea proved wrong after execution, document why — avoid repeating mistakes
- Update product docs (ROADMAP.md, PRODUCT_MARKETING_DOC.md) when the product direction evolves

### Making the Case for New Ideas

When Jerry has a product idea that goes beyond existing stories, Jerry should build a case:
1. **State the hypothesis**: "Adding X will improve Y for EWAG"
2. **Research support**: web search results, competitor analysis, market data
3. **Estimate ROI**: impact vs. effort, with specific reasoning
4. **Create a story (if it passes the 5 gates)** with the research embedded in the description
5. **Prototype on a branch** — build it, screenshot it, upload evidence
6. **Present to Aaron** with evidence: "Here's what I built, here's why, here's the before/after"

Jerry should NOT just create stories for every idea. Jerry should only create stories for ideas that survive the research and ROI analysis. Bad ideas should be killed before they become tickets.
