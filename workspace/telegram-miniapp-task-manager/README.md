# Telegram Mini App – Mission Control Prototype

A standalone mobile-first prototype for a Telegram Mini App task manager.

## What it is

A themed mini app inspired by:
- Nintendo / retro game HUD energy
- dark glassy cards like `glassclaw.app`
- a lightweight crew dashboard for Aaron, Taylor, Jerry, and agents

## Features in this prototype

- Crew dashboard with current + next task
- Upcoming meetings panel
- Priority queue panel
- Tap-to-cycle queue states
- Telegram Web App API hooks (`ready`, `expand`, colors, haptics)
- Pure HTML/CSS/JS so it can be hosted anywhere fast

## Files

- `index.html`
- `styles.css`
- `app.js`
- `game-map.html`
- `game-map.css`
- `hybrid-map.html`
- `hybrid-map.css`
- `world-map-v2.html`
- `world-map-v2.css`
- `world-map-v2.js`
- `world-map-v3.html`
- `world-map-v3.css`
- `final-miniapp.html`
- `final-miniapp.css`
- `final-miniapp.js`

`game-map.html` is the first scene-based direction: less dashboard, more animated tactical map / overworld.

`hybrid-map.html` is the Nintendo-map + Factorio-network blend: playful terrain, roads between zones, and moving packets between crew stations.

`world-map-v2.html` is the simplified direction based on Aaron's feedback: Jerry as the central dispatcher hub, Aaron left, Taylor right, Meetings top, with a cleaner Nintendo-style world map and simple flowing packets.

In V2, the Aaron / Taylor / Jerry / Meetings nodes are tappable and open a lightweight in-map detail panel with task or meeting info plus a link out to the deeper task manager.

`world-map-v3.html` is the more polished overworld pass: less webby, more Nintendo-map-like, with cleaner terrain, stronger landmark icons, simpler label placement, and the same tap-to-open node details.

`final-miniapp.html` is the packaged Telegram Mini App version: polished overworld scene, tappable Aaron/Taylor/Jerry/Meetings nodes, task + meeting detail modal, deeper task-manager links, and Telegram Web App hooks (`ready`, `expand`, colors, haptics).

## Local preview

From this folder:

```bash
python3 -m http.server 8123
```

Then open either:

```text
http://localhost:8123
http://localhost:8123/game-map.html
http://localhost:8123/hybrid-map.html
http://localhost:8123/world-map-v2.html
http://localhost:8123/world-map-v3.html
http://localhost:8123/final-miniapp.html
```

## To turn this into a real Telegram Mini App

1. Host this folder on HTTPS
2. Register/set your Telegram bot web app URL via BotFather
3. Launch it from an inline button or menu button
4. Replace mock data in `app.js` with real task + calendar sources
5. Optionally add:
   - auth/session binding
   - editable task cards
   - meeting detail drawer
   - agent activity log
   - pixel-art avatars / sprite sheets
   - save state to API/backend

## Next recommended upgrade

Convert this prototype into a real app stack:
- Vite + React
- a small API layer
- Telegram Mini App SDK helpers
- live task/calendar adapters

That would make it easier to maintain and polish further.
