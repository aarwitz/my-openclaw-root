# NEW_ARCHITECTURE_CRITIQUE.md

Date: 2026-05-24
Scope: review of our hybrid ACP/Codex lane design against OpenClaw docs.
Sources: codex-harness, codex-harness-reference, codex-harness-runtime, providers/openai, tools/acp-agents (and -setup).

## TL;DR

The hierarchy (Dwight orchestrates -> Resi/Jerry execute -> spawn ACP for heavy coding) is sound. But two design statements we have written are technically wrong against the docs and should be corrected before more wiring lands:

1. "Codex" is not a fallback lane separate from the OpenClaw agents. Codex is the runtime of those OpenClaw agents themselves when they use `openai/*` model refs.
2. ACP and "native Codex spawn_agent" are different escalation paths, with very different cost, auth, and tool surfaces. We currently only encode ACP; we should encode at least 3 lanes.

The hybrid concept is good. The lane labels and routing semantics need a small revision so they map cleanly to what OpenClaw actually does at runtime.

## What the docs say that affects our design

1. `openai/gpt-5.5` agent turns run through the bundled `codex` plugin (Codex app-server) by default — not through "Pi". Source: providers/openai "Naming map", "Route summary", "Status indicator".
2. The OpenClaw agent (Resi, Jerry, etc.) is the runtime host. Codex app-server is the model loop inside that runtime. They are layered, not alternatives. Source: codex-harness-runtime "Overview".
3. Each OpenClaw agent gets its own per-agent `CODEX_HOME`. So Resi and Jerry have independent Codex auth state and thread state. Source: codex-harness-reference "Auth and environment isolation".
4. Native Codex already exposes `spawn_agent` as its primary subagent surface. OpenClaw keeps `sessions_spawn` available as well. Source: codex-harness "Commands and diagnostics" (dynamic tools section) and codex-harness-reference "Dynamic tools".
5. ACP is the external-harness path. `runtime: "acp"` plus `agentId: "<harness>"` spawns an external coding harness (Claude Code, Cursor, Copilot, Codex-via-ACP, etc.). Source: tools/acp-agents "Start ACP sessions".
6. ACP from a sandboxed session is blocked. Your gateway currently runs `sandbox.mode: "off"` so this is not blocking now, but it constrains future sandbox plans. Source: tools/acp-agents "Sandbox compatibility".
7. Non-interactive ACP sessions must have `plugins.entries.acpx.config.permissionMode = "approve-all"` or `nonInteractivePermissions = "deny"`, otherwise writes/exec abort with `AcpRuntimeError: Permission prompt unavailable in non-interactive mode`. Source: tools/acp-agents "Troubleshooting" + acp-agents-setup "Permission configuration".
8. Single-use Codex OAuth refresh tokens are fragile. Multiple concurrent ACP sessions and gateway restarts can corrupt them. Source: your existing user memory rule (openclaw-gateway-rules) plus codex-harness "Auth and environment isolation".
9. `auth.order.openai` lets the same `openai/*` model run on subscription first, then API key, while staying on the Codex harness. Source: providers/openai "Config example".
10. `agentRuntime.id: "codex"` is a fail-closed flag for "must run on Codex." Without it, OpenClaw can fall back to Pi if Codex is unavailable. Source: codex-harness "Routing and model selection".

## Where our current design is wrong or fragile

1. Lane labels mislead.
   - We labeled lanes `acp-worker` and `codex`. But the "codex" lane is really "run inline inside the assigned OpenClaw agent's own Codex turn." It is not a separate runtime. It is the default runtime.
   - Effect: future readers (and future model agents) may think we are "switching to Codex" when Codex was already running.
   - Recommended rename:
     - `inline` = Resi/Jerry handle it inside their normal Codex-backed turn.
     - `acp-external` = spawn an external ACP harness (Cursor, Claude Code, Copilot ACP, etc).
     - `codex-subagent` = use native Codex `spawn_agent` to fork a Codex child for this work.
     - `oc-subagent` = `sessions_spawn` default subagent (sandboxed OpenClaw-native).

2. We only encoded one escalation path (ACP), but the docs make `spawn_agent` (native Codex subagent) the primary subagent surface and keep `sessions_spawn` as a secondary surface. We are skipping the cheapest escalation that already lives inside the same Codex thread.

3. Default ACP harness `cursor` requires `cursor-agent acp` to be installed and authed on this host. Choosing it as default without verifying installation will produce confusing first-run failures. Same applies if we ever default to `copilot` or `claude` without confirming auth.

4. We did not require ACP permission config. If ACP permissions are not pre-set to `approve-all` or `nonInteractivePermissions=deny`, the very first heavy task will likely fail with a non-interactive permission prompt error.

5. We did not set `agentRuntime.id: "codex"` anywhere. If Codex is briefly unavailable, Resi/Jerry can silently fall back to Pi and produce different behavior. For a coding-quality-sensitive design we probably want fail-closed Codex.

6. We did not set `auth.order.openai`. Without it, hitting a Codex usage limit during a long task can fail the run entirely instead of rotating to an API-key profile while staying on Codex.

7. ACP concurrency. Default `acp.maxConcurrentSessions: 8`. If Dwight launches many ACP workers in parallel against the same Codex OAuth profile, we risk both the concurrency cap and Codex token corruption. We should pin per-profile ACP concurrency and prefer ACP harnesses that do not consume the Codex OAuth (Cursor/Claude/Copilot) for parallel heavy work.

8. ACP target naming collision risk. ACP has a built-in `codex` agentId target and OpenClaw has the `codex` plugin id. We should NEVER name an OpenClaw agent `codex` to avoid confusion across config layers (`agents.list[].id`, `agentId` for ACP, `agentRuntime.id`, plugin id).

## What the design gets right

1. Dwight as orchestrator is correct. Codex docs explicitly support per-agent runtime isolation and the "main session is the coordinator" pattern.
2. Per-owner agents (Resi for iOS, Jerry for internal tooling) cleanly map to per-agent `CODEX_HOME`, independent ACP allowlists, and independent auth profiles. This is exactly what OpenClaw is designed for.
3. Spawning external ACP for "heavy/risky" work is the right escalation path when you want to avoid burning the owner agent's Codex thread/budget.
4. Owner-aware Codex lane (Jerry vs Resi) is correct because each agent has independent `CODEX_HOME`, model selection, and thread state.
5. Telegram-as-UI + OpenClaw-as-orchestrator + Codex-as-runtime + ACP-as-escalation is the same shape OpenClaw recommends in providers/openai and tools/acp-agents.

## Recommended adjustments (concrete, small)

1. Rename lanes in the router from `acp-worker | codex` to `inline | codex-subagent | acp-external`. Default order:
   - small/low-risk -> `inline`
   - medium with >1 file changes -> `codex-subagent` (cheap escalation in same Codex thread)
   - heavy, risky, multi-file, multi-day, or user-tagged "heavy" -> `acp-external`
   - explicit ACP-unavailable case -> downgrade to `codex-subagent` (still Codex) instead of "inline Codex" only.

2. Make ACP permission config a precondition. Add a one-time setup step (or doctor check in our scripts):
   - `openclaw config set plugins.entries.acpx.config.permissionMode approve-all`
   - `openclaw config set plugins.entries.acpx.config.nonInteractivePermissions deny`
   - Pick the most permissive value you are comfortable with; both are needed because non-interactive ACP cannot show a prompt.

3. Set `auth.order.openai` to subscription then API key for Resi, Jerry, and Dwight. Without this, an exhausted subscription stops the run instead of rotating profiles.

4. Add fail-closed Codex policy on the owner agents we care about:
   - `models.providers.openai.agentRuntime.id = "codex"` globally, or per-agent under `agents.list[].model` and a matching runtime pin.
   - This avoids silent Pi fallback when Codex is briefly down.

5. Decide a default ACP harness per owner, not a single global default:
   - Resi (iOS) -> probably `claude` or `cursor` depending on which is auth'd and which performs best on Swift.
   - Jerry (internal tooling) -> probably `cursor` or `copilot`.
   - Default to `--acp-available false` until each owner's ACP harness is verified by `/acp doctor`.

6. Pin ACP concurrency:
   - `acp.maxConcurrentSessions` set to a small number (2-4) initially.
   - For heavy parallel work, prefer ACP harnesses that do NOT consume the Codex OAuth (Cursor/Claude/Copilot) to avoid your existing single-use refresh token corruption risk.

7. Never name an OpenClaw agent `codex`. Reserve that id for the ACP target and the plugin.

8. Treat `codex-subagent` as the primary escalation, ACP as the secondary. This is what the Codex docs literally describe: native `spawn_agent` is the primary subagent surface. Our current scripts treat ACP as the primary escalation, which is backwards relative to docs.

## Token safety reminder (your existing rule)

1. Do not restart the gateway to apply ACP/Codex config changes during active runs. Use hot reload or `~/.openclaw/scripts/safe-restart.sh` per `openclaw-gateway-rules`.
2. Single-use Codex OAuth refresh tokens are the most fragile element in this whole stack. Any change that increases concurrent Codex turns (more ACP, more `spawn_agent`) increases token rotation pressure. Plan API-key backup before scaling parallelism.

## Suggested next actions

1. Rename lanes in `scripts/select-coding-lane.sh` and `scripts/launch-coding-task.sh` to `inline | codex-subagent | acp-external`.
2. Add a preflight script that sets ACP permission keys and verifies `/acp doctor` for the chosen ACP harness.
3. Add `auth.order.openai` and `agentRuntime.id: "codex"` to `openclaw.json` under the relevant agents.
4. Decide per-owner ACP target (Resi vs Jerry) and store it in the launcher metadata.
5. Add a `codex-subagent` execution path that calls `spawn_agent` through the owner agent's Codex turn instead of routing to external ACP.

## Bottom line

Architecture is right. Vocabulary and one escalation path are wrong. Fix the lane names, add ACP permission preconditions, add `auth.order.openai` and `agentRuntime.id: "codex"`, and add the `codex-subagent` lane. After that the design is aligned with what OpenClaw is actually doing under the hood, and the Telegram/Dwight/owner/ACP hierarchy you described becomes the natural and efficient way to drive it.
