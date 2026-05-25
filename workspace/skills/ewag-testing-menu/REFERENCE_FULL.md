---
name: ewag_testing_menu
description: "Interactive EWAG testing & build menu with Telegram inline buttons. Sends a button UI for screenshots, recordings, tests, and build operations."
user-invocable: true
metadata: {"openclaw":{"emoji":"🧪","os":["linux"],"requires":{"channels":["telegram"]}}}
---

# EWAG Testing Menu — Interactive Buttons

This skill provides a **discoverable, interactive testing interface** for EWAG on Telegram.

## What It Does

Displays a menu with inline buttons that users click to:
- **Capture screenshots** (home, coaching, nutrition, community, rewards, all)
- **Record videos** (any tab)
- **Run tests** (smoke, all, or specific tests)
- **Build operations** (build, clean, branch status)
- **Quick actions** (node health check, toggle build mode)

Each button press triggers a callback → Jerry dispatches to the appropriate script.

## When to Invoke This

- User types: `/menu`, `/tests`, `/capture`, or mentions "testing menu"
- Jerry judges that a menu would aid navigation (e.g., "what are my options?")
- Recurring operations need discoverable UI (not just `/bash` text commands)

## Skill Behavior

### Step 1: Prepare the menu structure

```json
{
  "title": "🧪 EWAG Testing Menu",
  "sections": [
    {
      "name": "📸 Screenshot",
      "buttons": [
        {"label": "Home", "callback": "capture_home"},
        {"label": "Coaching", "callback": "capture_coaching"},
        {"label": "Nutrition", "callback": "capture_nutrition"},
        {"label": "Community", "callback": "capture_community"},
        {"label": "Rewards", "callback": "capture_rewards"},
        {"label": "All Tabs", "callback": "capture_all"}
      ]
    },
    {
      "name": "🎬 Recording",
      "buttons": [
        {"label": "Home", "callback": "record_home"},
        {"label": "Coaching", "callback": "record_coaching"},
        {"label": "Rewards", "callback": "record_rewards"}
      ]
    },
    {
      "name": "🧪 Tests",
      "buttons": [
        {"label": "Smoke", "callback": "test_smoke"},
        {"label": "All", "callback": "test_all"},
        {"label": "List", "callback": "test_list"}
      ]
    },
    {
      "name": "🔨 Build",
      "buttons": [
        {"label": "Build Current", "callback": "build_current"},
        {"label": "Clean", "callback": "build_clean"},
        {"label": "Status", "callback": "build_status"}
      ]
    }
  ]
}
```

### Step 2: Construct Telegram inline keyboard JSON

Build the `inline_keyboard` structure for OpenClaw's Telegram tool:

```json
{
  "inline_keyboard": [
    [
      {"text": "📸 Home", "callback_data": "capture_home"},
      {"text": "📸 Coaching", "callback_data": "capture_coaching"},
      {"text": "📸 Nutrition", "callback_data": "capture_nutrition"}
    ],
    [
      {"text": "📸 Community", "callback_data": "capture_community"},
      {"text": "📸 Rewards", "callback_data": "capture_rewards"},
      {"text": "📸 All Tabs", "callback_data": "capture_all"}
    ],
    [
      {"text": "🎬 Home", "callback_data": "record_home"},
      {"text": "🎬 Coaching", "callback_data": "record_coaching"},
      {"text": "🎬 Rewards", "callback_data": "record_rewards"}
    ],
    [
      {"text": "🧪 Smoke", "callback_data": "test_smoke"},
      {"text": "🧪 All", "callback_data": "test_all"},
      {"text": "📋 List", "callback_data": "test_list"}
    ],
    [
      {"text": "🔨 Build", "callback_data": "build_current"},
      {"text": "🧹 Clean", "callback_data": "build_clean"},
      {"text": "📊 Status", "callback_data": "build_status"}
    ]
  ]
}
```

### Step 3: Send the menu message

Use `/message` action to send the message with buttons (via OpenClaw's Telegram tool):

```
/message to=<chat_id> content="🧪 EWAG Testing Menu\nSelect an operation:" buttons='<inline_keyboard_json>'
```

Or if using agent context (recommended):

```python
await send_telegram_menu(
    content="🧪 EWAG Testing Menu\nSelect an operation:",
    inline_keyboard=keyboard_json
)
```

### Step 4: Handle callback queries

When a user clicks a button, Telegram sends a callback query with:

```json
{
  "type": "callback",
  "callback_id": "<unique_callback_id>",
  "callback_data": "capture_home",
  "from_user_id": 6043080629,
  "chat_id": -4995650408,
  "message_id": 12345
}
```

This arrives as an **inbound agent message** with the callback_data in the context.

### Step 5: Dispatch to appropriate handler

Jerry's system prompt contains the dispatch logic. Example:

```
if callback_data starts with "capture_":
  view = callback_data.split("_")[1]
  run: /bash ~/.openclaw/scripts/ewag-capture.sh {view}

if callback_data starts with "record_":
  view = callback_data.split("_")[1]
  run: /bash ~/.openclaw/scripts/ewag-capture.sh {view} record

if callback_data starts with "test_":
  test_cmd = callback_data.split("_")[1]
  run: /bash ~/.openclaw/scripts/ewag-test.sh {test_cmd}

if callback_data starts with "build_":
  build_cmd = callback_data.split("_")[1]
  run: /bash ~/.openclaw/scripts/ewag-build.sh {build_cmd}
```

---

## Usage Examples

### Send the menu to users

**In conversation:**
- User: "What testing options do I have?"
- Jerry: "Let me show you the testing menu..." [sends menu]

**Programmatically:**
- Aaron asks Jerry to "open the testing menu"
- Jerry invokes this skill → menu appears with buttons

### User clicks button

1. User clicks "📸 Home" button
2. Button's `callback_data` is `capture_home`
3. Telegram delivers callback query to OpenClaw
4. Jerry receives callback, parses it, runs: `/bash ~/.openclaw/scripts/ewag-capture.sh home`
5. Result posted back to chat with link to Drive screenshot
6. Menu reappears (optionally with "Run another?" button for UX)

### Chaining operations

After a screenshot completes:
```
✅ Screenshot captured: [home link]

Want to run another?
[📸 Home] [📸 Coaching] ... [❌ Close]
```

---

## Implementation Notes

### Button limit

Telegram inline keyboards support up to **8 buttons per row** and **no limit on rows** (in practice, 20-30 rows work well). This menu uses 3 buttons per row for readability.

### Callback data constraints

- Max 64 bytes per button
- Must be unique per menu instance (or stateless per message)
- No spaces; use underscores or dashes

### Error handling

If a callback data doesn't match a known handler:
1. Jerry logs: "Unknown callback: {callback_data}"
2. Sends neutral response: "Not recognized. Try the menu again."
3. Optionally resends the menu

### Menu persistence

The menu does NOT auto-update. After a button press:
- Telegram removes the loading indicator
- Jerry sends a result message
- User must send another message or click "View Menu" to refresh

**Alternative:** Include a refresh button at the bottom of the menu.

---

## Related Skills

- `ewag_capture` — called for `capture_*` actions
- `ewag_test` — called for `test_*` actions
- `ewag_build` — called for `build_*` actions

## Callback Data Reference

| callback_data | Action | Script |
|---|---|---|
| `capture_home` | Screenshot home tab | `ewag-capture.sh home` |
| `capture_coaching` | Screenshot coaching tab | `ewag-capture.sh coaching` |
| `capture_nutrition` | Screenshot nutrition tab | `ewag-capture.sh nutrition` |
| `capture_community` | Screenshot community tab | `ewag-capture.sh community` |
| `capture_rewards` | Screenshot rewards tab | `ewag-capture.sh rewards` |
| `capture_all` | Screenshot all 5 tabs | `ewag-capture.sh all` |
| `record_home` | Record home tab | `ewag-capture.sh home record` |
| `record_coaching` | Record coaching tab | `ewag-capture.sh coaching record` |
| `record_rewards` | Record rewards tab | `ewag-capture.sh rewards record` |
| `test_smoke` | Run smoke tests | `ewag-test.sh smoke` |
| `test_all` | Run all UI tests | `ewag-test.sh all` |
| `test_list` | List available tests | `ewag-test.sh list` |
| `build_current` | Build current branch | `ewag-build.sh build` |
| `build_clean` | Clean DerivedData | `ewag-build.sh clean` |
| `build_status` | Quick node health check | `ewag-build.sh status` |
