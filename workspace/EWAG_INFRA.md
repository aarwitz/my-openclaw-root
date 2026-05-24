# EWAG Infrastructure — Single Source of Truth

Last updated: 2026-05-06

This is the **only** doc Jerry should consult for EWAG iOS build/test/capture infrastructure. If something contradicts this file, this file wins.

---

## Architecture (one paragraph)

ResiLife is an iOS app. iOS builds require macOS, so we use a two-host setup: **Linux** (this gateway) is the control plane for code, planning, Task Manager, GitHub, and orchestration. **Taylor's Mac** is an execution appliance that runs `ios-agent` over the OpenClaw node protocol for build / test / screenshot / record. No LLM is involved in the build/capture pipeline — it's fully deterministic shell scripts.

## Hosts & Paths

| Item | Value |
|---|---|
| Linux gateway IP (Tailscale) | `100.110.113.91` |
| Mac node user@IP | `taylorolsen-vogt@100.125.133.123` |
| Mac node name | `ios-build-node` |
| ios-agent path on Mac | `/Users/taylorolsen-vogt/ios-agent/ios-agent` |
| iOS repo on Mac | `/Users/taylorolsen-vogt/iosApp` |
| iOS repo on Linux | `~/repos/EWAG-dev-iosApp` |
| Gateway port | `18789` (must bind `0.0.0.0`) |
| Simulator | iPhone 16 Pro Max, 1320×2868, iOS 18.3 |

## Git Topology — IMPORTANT

```
GitHub (EWAG-dev/iosApp.git)
   ↑ push
Linux repo (~/repos/EWAG-dev-iosApp)        ← origin = GitHub
   ↑ fetch (Mac's "origin" points here, not GitHub)
Mac repo (/Users/taylorolsen-vogt/iosApp)   ← origin = aaron@100.110.113.91:~/repos/EWAG-dev-iosApp
```

- The Mac never talks to GitHub directly.
- **Code must land on Linux first** (commit + push to GitHub), then `ios-agent build --branch X` fetches it onto the Mac via Linux.
- **Never push from Mac → Linux** (Git rejects pushes into a non-bare checked-out branch).
- Linux has a `post-merge` git hook that runs `~/.openclaw/scripts/reconcile-task-manager-with-git.py --apply` and deletes merged feature branches.

## ios-agent — Ownership & Update Path

**ios-agent is infrastructure, not a skill.** All EWAG scripts and skills call into it.

| Aspect | Where |
|---|---|
| Source of truth (Linux, version-controlled) | `~/.openclaw/workspace/ios-agent-v2.sh` |
| Deployed copy on Mac | `/Users/taylorolsen-vogt/ios-agent/ios-agent` |
| Update mechanism | `ios-agent self-update` over node protocol (base64 streamed) |
| First-time deploy | `scp` then `chmod +x` (manual, Aaron) |
| Logs | `~/ios-agent/logs/` (Mac), `~/.openclaw/logs/node.log` (Mac daemon) |

**ios-agent invariants:**
- Enforces `-parallel-testing-enabled NO` (sim clones fail with `RequestDenied`).
- Returns `EXPORT_FORMAT` and `EXPORT_CORRECT_EXT` — Jerry must use these, never assume `.png`.
- Tab navigation uses coordinate taps (`dx = tabIndex/5.0 + 0.1`), not button lookup, to avoid SwiftUI animation idle-wait hangs.
- Rewards tab is `buttons["gift"]` (SF Symbol id), not `buttons["Rewards"]`.

## Mac Node Daemon

Runs `openclaw node run` as launchd service `ai.openclaw.node`.
- Plist: `~/Library/LaunchAgents/ai.openclaw.node.plist` (RunAtLoad, KeepAlive)
- Logs: `~/.openclaw/logs/node.log`, `node.err.log`
- Node.js: v22 via Homebrew (`/opt/homebrew/opt/node@22/bin/node`)
- **OpenClaw version on Mac must match the gateway** — verify with `openclaw --version` on both. If behind, on the Mac: `npm update -g openclaw && launchctl unload ~/Library/LaunchAgents/ai.openclaw.node.plist && launchctl load ~/Library/LaunchAgents/ai.openclaw.node.plist`

## Mac One-Time Setup

**Accessibility permissions** (System Settings → Privacy & Security → Accessibility): `node`, `/usr/libexec/sshd-keygen-wrapper`, `Terminal`, `Xcode`. Without `sshd-keygen-wrapper`, tap/swipe/input commands silently hang.

**Exec allowlist** (`~/.openclaw/exec-approvals.json`): only `ios-agent`, `node`, `xcrun`, `xcodebuild`, `git`, `uname`. **No shell wrappers** (`bash -c`, `sh -c` are blocked).

## Mac Node Concurrency — CRITICAL

- **One simulator operation at a time.** Never run two node commands concurrently.
- Never run `test-all` and a targeted test simultaneously.
- Each heartbeat must serialize node ops: build → run → ONE test → extract.
- If a node op times out, **stop and report — do not retry in a loop.**

## Operator Surface (what Jerry actually uses)

Jerry never calls `ios-agent` directly except in fallback. Use these in priority order:

### 1. Deterministic shell scripts (preferred)
| Script | Purpose |
|---|---|
| `~/.openclaw/scripts/ewag-build.sh [branch\|clean\|status]` | Build / clean / branch info |
| `~/.openclaw/scripts/ewag-test.sh <case\|list\|smoke\|all>` | XCUITest runner |
| `~/.openclaw/scripts/ewag-capture.sh <view> [screenshot\|record\|scroll] [--build] [--branch X] [--no-upload] [--clean]` | Screenshot / recording / scroll → Drive |
| `~/.openclaw/scripts/credential-preflight.sh` | All-in-one auth + gateway + node health check |

These scripts SSH to the Mac (key-based, `BatchMode=yes`) and use SSH keepalives for hang detection (`ServerAliveInterval=15`, `CountMax=20` ≈ 5 min). `timeout(1)` doesn't exist on macOS.

### 2. Slash commands (Telegram)
- `/ewag_build [args]` → wraps `ewag-build.sh`
- `/ewag_test [args]` → wraps `ewag-test.sh`
- `/ewag_capture <view> [opts]` → wraps `ewag-capture.sh`
- `/ewag_capture <view> record` → recording (the old `/ewag_record` was collapsed into capture on 2026-05-06)
- `/menu` → renders inline button UI (`ewag-testing-menu` skill)
- `/bash <cmd>` → raw shell (zero LLM)

### 3. Direct ios-agent (only when scripts can't express what's needed)
```
/exec host=node node=ios-build-node timeout=300 \
  /Users/taylorolsen-vogt/ios-agent/ios-agent test --case ResidentUITests/testRewardsScreenshot
```
Use timeout=300 for tests. Never wrap in `bash -c`.

## ios-agent Command Surface

**Build/run:** `build [--branch X]`, `run`, `clean`, `branch`
**Test (timeout=300):** `test --case <Class/method>`, `test-all`, `test-list`, `test-results`
**Capture:** `screenshot`, `record`, `record-stop`, `record-test --case <Class/method>`
**Export:** `export-screenshot`, `export-recording` (returns base64 to gateway)
**Other:** `deviceinfo`, `self-update`, `calibrate` (local only)
**Interaction:** `tap`, `swipe`, `input`, `home`, `openurl`

## Test Catalog (current)

`ResiLifeUITests` target. Capture pipeline runs with `--live-demo-auth` by default.

**Resident screenshots** (`ResidentUITests`, `UI_TEST_ROLE=resident`): testHomeScreenshot, testCoachingScreenshot, testNutritionScreenshot, testCommunityScreenshot, testRewardsScreenshot, testAllTabsScreenshot, testProfileScreenshot, testChallengesScreenshot, testConnectorScreenshot, testCalendarScreenshot, testNotificationsScreenshot, testBookmarksScreenshot, testMessagesScreenshot, testSettingsScreenshot, testSpaScreenshot, testPoolScreenshot (Spa/Pool are amenity-gated and skip if N/A), plus `_Scrolled` variants and `testCoachingScrollThrough` (used by `scroll` mode).

**Owner screenshots** (`OwnerUITests`, `UI_TEST_ROLE=owner`): testOwnerDashboardScreenshot, testOwnerRevenueScreenshot, testOwnerEngagementScreenshot, testOwnerRetentionScreenshot, testOwnerOperationsScreenshot, testOwnerProgramsScreenshot, testOwnerAllTabsScreenshot, testOwnerSideMenuScreenshot, testOwnerSmokeScreenshot.

**Resident interaction** (`ResidentInteractionTests`): testSideMenuNavigateToChallenges, testSideMenuNavigateToConnector, testHomeFeaturedEventTap, testCoachingCarouselSwipe, testNutritionWhatToEatCards, testRewardsEarnRedeemToggle, testFullAppWalkthrough, testRapidTabSwitching.

**Direct `ewag-capture.sh` view targets:** home, coaching, nutrition, community, rewards, profile, challenges, connector, calendar, notifications, bookmarks, messages, settings, spa, pool, all.

## Google Drive: ResiLife UI Artifacts

**Root** (do not upload here): `1rQIIj8sgbrnUhGP1MKzSmsMdHH4xfDDH`

| Subfolder | ID |
|---|---|
| Home | `1yP0DcfeCBlhP8vh3IkfZoZPGcHgb8F2K` |
| Coaching | `1GzXle3mOIc_N4DqzHnXgLX0Iu5OrP4Tf` |
| Nutrition | `1i6N7dADbzS_HmnKS465HMYAhVW0nGlcs` |
| Community | `1gSDBT1LFWRM_FU8QICV9GBe0Z39dDtRh` |
| Rewards | `13Ud5sLZqgGWhHxVzqw-juxJiiTD6pbiH` |
| Profile | `1HmaXP0Su_tw1iW5AGzwCbkCQxARydyQw` |
| Challenges | `1RErODLBhd5sSZH_yUrBX5FNkkFPKs6Cz` |
| Connector | `14t72WBeZZirqgfMcZ2lkgCiYHDBykNLM` |
| Calendar | `1p3i4dNL6hv5mYuWMGtbGvvEPKboF6y0I` |
| Notifications | `1oz1120HAQxXbRXeofZ81VOGpqSuoRF_7` |
| Bookmarks | `14ka69RawnnYHbpIeZK6DlEOliyUPpFOi` |
| Messages | `1lNdzEV2c4FyMdpVTiD1E8sfanyEXgv7X` |
| Settings | `1slKSGq5YthXYciL0pq6lfUnwt92quB5K` |
| Spa | `12H7hJPPHTXSSrpkLvf6Pr33-PUU7EvIL` |
| Pool | `1conYdCrJd4LJeg9nDp4Vyztz_LZ4D99X` |
| Recordings (WebM/MP4) | `1eHsFkQdD2bfO2pj_ckW32zBpw-zqyd_c` |

Naming: `<view>-YYYY-MM-DD.<ext>`. Recordings always go in `Recordings`, regardless of view.

## Standard Dev Loop

1. Edit code on Linux (`~/repos/EWAG-dev-iosApp`)
2. Commit + push to GitHub
3. `ewag-build.sh build <branch>` (Mac fetches from Linux, not GitHub)
4. `ewag-test.sh <case>` or `test-all`
5. Fix failures → rebuild → retest (max 3 retries)
6. `ewag-capture.sh all` for screenshots → Drive
7. PR with Aaron as reviewer when tests pass + screenshots look right

## Failure Recovery Quick-Ref

| Symptom | Fix |
|---|---|
| Build fail | Read log → fix → rebuild (max 3 retries) |
| Sim fail | `ewag-build.sh clean` → build → run |
| Node unreachable | Check Mac power + Tailscale + `launchctl list \| grep openclaw`; verify version match |
| Gateway tailnet-only | `openclaw config set gateway.bind lan` (hot-reloads) |
| Auth drift | Run `~/.openclaw/scripts/credential-preflight.sh` |
| Token corruption | Use `~/.openclaw/scripts/safe-restart.sh`, never `systemctl restart` |

## Adding a New Screenshot View

1. Add Swift test method in the appropriate `*UITests.swift` on Mac
2. Create Drive subfolder; capture its ID
3. Add view → test, view → folder-id, view → attachment-name entries to the three associative arrays in `ewag-capture.sh`
4. Test from Telegram: `/ewag_capture <newview>`

## Telegram Button Menu

The `ewag-testing-menu` skill renders an inline keyboard whose `callback_data` values map 1:1 to the scripts above (`capture_<view>`, `record_<view>`, `test_<cmd>`, `build_<cmd>`). Jerry parses the callback and dispatches. Limits: 64-byte callbacks, ~8 buttons/row, 30s Telegram bot timeout (backend continues past timeout).

## Hard Rules

- Linux is the control plane. Mac is execution-only for iOS.
- Never use "Linux has no swift" as a final blocker — route to ios-build-node.
- Never `bash -c` / `sh -c` on the node (allowlist blocks them).
- Never restart with `systemctl restart` — use `~/.openclaw/scripts/safe-restart.sh`.
- Never push directly to `main`.
- Reconcile before resuming a non-done EWAG issue: `~/.openclaw/scripts/reconcile-task-manager-with-git.py --apply`.
