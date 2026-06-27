# Montra-PM - TOOLS

- Use coding and messaging tools to implement and communicate progress.
- Prefer deterministic scripts and verifiable checks for operational tasks.
- Keep outputs concise and practical.

## Hard Execution Boundary

- For MONTRA dev/build work, execute only through:
	- `/home/aaron/.openclaw/scripts/montra-mac-safe.sh run -- <command>`
- This script always runs on Mac host `taylorolsen-vogt@100.125.133.123` in:
	- `/Users/taylorolsen-vogt/repos/MONTRA`
- Never execute MONTRA build/dev commands directly on Linux.
- Never operate on paths outside `/Users/taylorolsen-vogt/repos/MONTRA`.

## Allowed Build Tooling (inside MONTRA root)

- Typical build/dev commands are allowed through the wrapper, including:
	- `git`, `xcodebuild`, `xcrun`, `swift`, `swiftlint`
	- `npm`, `npx`, `yarn`, `pnpm`, `node`
	- `bundle`, `pod`, `fastlane`, `ruby`, `gem`, `python3`
	- `plutil`, `defaults`, `PlistBuddy`
- Safety constraint: shell metacharacters are blocked (`;`, `|`, `&`, `$`, etc).
- If a command is denied but needed for MONTRA build flow, update the allowlist explicitly instead of bypassing the wrapper.

## Sync helpers

- Pull Mac -> Linux mirror:
	- `/home/aaron/.openclaw/scripts/montra-mac-safe.sh sync-pull`
- Push Linux mirror -> Mac:
	- `/home/aaron/.openclaw/scripts/montra-mac-safe.sh sync-push`
