# EWAG + Lidi Boundary Contract

Date: 2026-05-31
Status: authoritative
Owner: Aaron

## Roles Table (Top-Level Authority)

| Role Group | Members | Can Operate In | Cannot Operate In | Notes |
|---|---|---|---|---|
| Founders (human) | Aaron, Taylor | RSL lane and EWAG/Lidi lane | N/A | Human override authority across both lanes |
| Interns (human) | Dhuri, Purva Pravin; Patel, Bhargav; Wen, Yongqian; Nguyen, Doan Duy Minh | EWAG/Lidi lane only | RSL lane | Intern scope is EWAG onboarding and delivery work |
| RSL bots | Jerry, Dwight, Resi, Druck, trading bots | RSL lane only | EWAG/Lidi lane | Must not deploy/manage EWAG/Lidi bots or interact with lidi-task-manager |
| Lidi bots (EWAG fleet) | EWAG/Lidi bot fleet | EWAG/Lidi lane only | RSL lane | Must not deploy/manage RSL bots |

## Credential Access Table

| Role Group | Direct Credential Access | Allowed Credential Domains | Denied Credential Domains | GitHub Auth Identity |
|---|---|---|---|---|
| Founders (human) | Yes | Cross-lane as needed | N/A | aaronclawrsl-bot (bots) and founder-managed human auth |
| Interns (human) | No | None | All credential files and tokens | No direct credential use |
| Lidi bots (EWAG fleet) | Yes, scoped | EWAG GoDaddy, EWAG Wix, Lidi Cloudflare, Lidi Task Manager auth, Mac node and ResiLife execution credentials | RSL-only credentials and systems not needed for EWAG scope | aaronclawrsl-bot |
| RSL bots | Yes, broad | All RSL credential domains required for RSL operations | Lidi Task Manager credentials, EWAG GoDaddy, EWAG Wix | aaronclawrsl-bot |

Credential principles:
- Interns never receive raw credentials.
- Bots receive least-privilege credentials by lane.
- Cross-lane credential sharing is blocked by default.
- All bot GitHub operations route through aaronclawrsl-bot.

## 1) Core truth

There is one operational Task Manager lane now.

Hosted lane:
- Bots: Jerry, Dwight, Resi, Druck, trading bots, EWAG/Lidi fleet
- Task Manager: https://tm.lidisolutions.ai
- Hosted repo: `aarwitz/lidi-task-manager`
- Dwight workspace copy may still exist locally for development/reference, but the hosted runtime is the source-of-truth path
- Retired local lane references (`http://127.0.0.1:8000`, `http://rsl:8000`) must not be used as the primary Task Manager path

Human exception:
- Aaron and Taylor are humans and can operate across both lanes when needed.
- This exception does not grant cross-lane permissions to bots.

## 2) Hard rules (no ambiguity)

- Launchers and automation must point to `https://tm.lidisolutions.ai`.
- Retired local Task Manager lane references must be removed from active configs as they are found.
- If a lane violation is found, stop feature work and fix isolation first.

Temporary enforcement mode:
- Resi is treated as RSL for now.
- Default EWAG owner-agent list is disabled until the dedicated EWAG launcher path is fully split and enabled.

Credential rules:
- Interns must not be given direct access to credentials, tokens, or secret files.
- Lidi bots must only load EWAG/Lidi credential surfaces.
- RSL bots must not load Lidi Task Manager, EWAG GoDaddy, or EWAG Wix credentials.
- Credential files and secret refs must be separated by lane and reviewed before enabling any new bot action.

## 3) Why this exists

We had a boundary violation: EWAG allowlist logic was added to the Dwight RSL launcher path. This document is the source of truth to prevent that from happening again.

## 4) Current verified EWAG state

- Lidi production reset completed.
- One active sprint: EWAG I.
- Five onboarding issues exist for the four interns plus Aaron.
- Mac node queue serialization and stale lock cleanup are in place.

## 5) Immediate cleanup checklist

- [x] Remove EWAG allowlist constants from /home/aaron/.openclaw/scripts/dwight-launch-from-issue.py
- [x] Keep Dwight launchers deny-focused only (no EWAG routing responsibility)
- [ ] Keep lane-specific launchers split by prefix and Task Manager endpoint
- [x] Run boundary regression tests and dry-run one launch per lane

Exit criteria:
- No EWAG repo references in Dwight RSL launchers.
- Boundary tests pass.
- RSL dry-run launch resolves only RSL repo targets.

## 6) Delivery priorities (EWAG)

- [x] Finish boundary cleanup first (current temporary mode active)
- [x] Implement floating Lidi bot UI in tm.lidisolutions.ai
- [x] Wire floating bot actions to EWAG-only endpoints (approval-gated drafts)
- [ ] Add queue visibility (queued/running/failed) as a dedicated operator surface
- [ ] Add native autolaunch state fields and endpoints in Lidi Task Manager
- [ ] Complete PR lifecycle -> TM status transitions
- [ ] Rehearse end-to-end flow for all 5 onboarding issues

## 6.2) Implementation Readiness Board

Lane isolation and policy:
- State: mostly complete
- Complete:
  - Dwight launcher no longer embeds EWAG allowlist policy logic
  - Default EWAG owner list is disabled
  - Resi is currently classified under RSL default owner set
  - Launch path requires explicit owner-agent (no blank-owner fail-open)
- Remaining:
  - Split and enable dedicated EWAG launcher path (not Dwight-prefixed)

Credential routing and identity:
- State: mostly complete
- Complete:
  - Bot GitHub routing unified to aaronclawrsl-bot across scripts and validators
  - Resi checks now validate against aaronclawrsl-bot
- Remaining:
  - Run a full environment auth recovery pass until preflight is green end-to-end

Security hardening for intern onboarding:
- State: mostly complete (backend path)
- Complete:
  - Public session auth required for issue, launch-control, comment, image, and sprint APIs
  - Lidi action draft/approve/cancel requires authenticated session identity (no unauth fallback actor impersonation)
  - Bot-only launch claim/result/release enforced with shared bot token
  - Non-owner users are restricted to editing assigned EWAG-scoped issues
  - EWAG allowlist enforced for repo_slug on create/update/create-via-Lidi
  - Internal username login is disabled by default
  - Password hashing moved from unsalted SHA256 to PBKDF2-SHA256 with legacy migration on login
  - CORS origins now explicit via environment configuration (no wildcard default)
  - Test-token exposure toggles gated behind non-production + explicit allow flag
- Remaining:
  - Mirror the same hardening controls in the worker runtime path if production traffic still terminates there

Lidi PM UX and automation:
- State: partially complete
- Complete:
  - Floating Lidi widget is present across core TM pages
  - Lidi conversation persistence and minimize/restore behavior implemented
  - Agents page has modal profile drill-in and human-blocker panel
  - Lidi draft/approve/cancel action API exists (approval-gated)
  - Agents modal can now pause/resume autolaunch per task via launch-control
- Remaining:
  - Add explicit queued/running/failed queue surface for operators
  - Add PR webhook-driven state sync to move TM states deterministically

Onboarding execution:
- State: ready for rehearsal
- Complete:
  - EWAG I sprint and five onboarding issues are seeded
- Remaining:
  - Execute full 5-story end-to-end rehearsal and capture evidence per story

Orchestration control loop:
- State: mostly complete
- Complete:
  - Lidi can draft and approve create issue and comment issue actions
  - Agent activity and human blocker visibility are present in UI surfaces
  - Native autolaunch claim/result/control endpoints are implemented
  - Launch-candidates polling endpoint is implemented for recurring pickup
  - Launch attempts require a decision-log comment on each attempt
  - Pre-PR attempt cap is enforced (default 5 attempts), then auto-pause
  - PR-aware transitions are implemented: PR opened -> in_review, PR merged -> done
- Remaining:
  - Deterministic PR lifecycle state sync from GitHub events/webhooks to remove manual ambiguity

Definition of "everything together" for this phase:
- One canonical plan (this file) matches script behavior, identity routing, and UI state.
- Boundary tests and lane regression pass.
- Launchers, credentials, and UI all operate under the same lane contract.
- Public onboarding path is protected by session auth and EWAG-scope authorization controls.

## 6.3) Orchestration Workflow (Operator + Lidi)

How orchestration is handled now:
- Lidi chat can draft actions and requires explicit approval before creating an issue or posting a comment.
- Issue execution orchestration for coding launch exists in launcher scripts, but native autolaunch state is not yet modeled in Lidi Task Manager entities.

Target workflow (the required lifecycle):
1. Create task:
  - Either manual issue create or Lidi draft then approve.
2. Refine task:
  - Edit title, scope, acceptance criteria, assignee, repo, and blocker fields.
3. Arm autolaunch:
  - Set issue as autolaunch-enabled when ready for agent pickup.
4. Launch and monitor:
  - Agent starts work, posts evidence, and updates branch and PR signal.
  - Human resolves blockers quickly and can edit task details mid-flight.
5. Review and quality gate:
  - Add reviewers when implementation and evidence are ready.
  - Require tests and reviewer approvals before merge.
6. Merge and close:
  - Merge after two reviewer approvals and passing tests.
  - TM status transitions to done with merge evidence.

Autolaunch state machine to implement in Lidi Task Manager:
- Fields:
  - auto_launch_enabled: boolean
  - launch_state: ready, queued, launched, failed, paused
  - launch_error: nullable text
  - launch_claimed_by: nullable text
  - launch_claimed_at: nullable timestamp
  - last_launch_at: nullable timestamp
  - last_launch_signature: nullable text
- Endpoints:
  - issue launch claim endpoint with optimistic signature check
  - issue launch result postback endpoint
  - issue pause and resume launch endpoint
- Guarantees:
  - idempotent launch claim per signature
  - one active launch claim at a time
  - explicit failure reason persisted for operator action
  - one decision-log comment required per launch attempt
  - pre-PR launch attempts capped at 5 by default (first pass + 4 retries), then auto-paused
  - recurring pickup supported via launch-candidates polling window (default 60 minutes)

Operator runbook for your desired process:
- Create or approve issue draft from Lidi.
- Edit issue until launch-ready.
- Toggle autolaunch on.
- Watch agent telemetry and blocker panel.
- Unblock quickly, update task if needed, and keep branch and PR moving.
- Add reviewers when evidence is ready.
- Merge after 2 approvals and passing tests.
- Confirm TM moves to done with PR and merge evidence.

## 6.1) Lidi PM Product Direction (Concise)

Primary PM bot identity:
- Bot name: Lidi
- Role: PM for lidi-task-manager workflows
- Scope: EWAG/Lidi lane only

Floating chat requirement (all pages):
- Lidi chat must float over every page at https://tm.lidisolutions.ai
- Default state is minimized (small launcher), expandable on click
- Conversation should persist across page navigation in the same session
- Tone and visual style: friendly, cute, modern, sleek, clearly AI-powered
- Not limited to the Agents page

Agents page redesign requirement:
- Keep the Agents page, remove fake/vanity metrics (for example: generic health stats)
- Agent cards should show useful live work signals only
- Show details progressively (not all at once)
- Hover behavior on cards for quick context
- Click behavior opens agent profile in a modal overlay (not a full standalone page)
- Modal must include close control (X)

Minimum useful agent card/profile data:
- Open PRs
- Tasks currently in progress
- Last time agent worked on a task
- Last time agent made a commit
- Human-required blockers currently assigned to that agent

Human intervention workflow requirement:
- Add a clear surface for blockers requiring human intervention
- Blockers should be first-class, visible, and triaged separately from normal task flow
- Lidi should surface blocker status and recommended next human action

Containerization and isolation requirement:
- Bot execution must remain containerized and lane-isolated to prevent repo collisions
- EWAG/Lidi containers may mount only EWAG/Lidi repos and credentials
- RSL containers may not mount EWAG/Lidi repos or EWAG-only credentials
- Isolation policy is fail-closed: if scope cannot be proven, execution must stop

## 7) Change-control policy

- Any change touching both RSL and EWAG files in one batch needs explicit written scope approval.
- Naming ownership is strict:
  - dwight-* or rsl-* means RSL-only
  - ewag-* or lidi-* means EWAG-only
- Per-lane constants stay in per-lane files.

Credential enforcement actions:
- Keep separate credential inventories per lane and do not mix references in launcher code.
- Enforce lane-specific secret refs in configuration and hooks, then fail closed on missing/invalid scope.
- Add a preflight check that prints which credential domains a bot run is allowed to touch and aborts on policy mismatch.
- Audit bot credential usage regularly and remove any credential not required for current lane scope.

## 8) Canonical file

This file is the source of truth for EWAG onboarding boundaries and execution scope:
- /home/aaron/.openclaw/workspace/docs/ewag-lidi-source-of-truth-plan.md
