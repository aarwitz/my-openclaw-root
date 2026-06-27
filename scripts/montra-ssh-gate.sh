#!/usr/bin/env bash
set -euo pipefail

ROOT="/Users/taylorolsen-vogt/repos/MONTRA"
cmd="${SSH_ORIGINAL_COMMAND:-}"

fail() {
  printf "[montra-ssh-gate] denied: %s\n" "$*" >&2
  exit 126
}

[[ -n "$cmd" ]] || fail "empty command"
[[ "$cmd" =~ [\;\&\|\<\>\`\$\(\)\{\}] ]] && fail "metacharacters blocked"

read -r -a argv <<<"$cmd"
[[ ${#argv[@]} -gt 0 ]] || fail "no argv"

first="${argv[0]}"
base="$(basename "$first")"

case "$base" in
  git) bin="/usr/bin/git" ;;
  xcodebuild) bin="/usr/bin/xcodebuild" ;;
  xcrun) bin="/usr/bin/xcrun" ;;
  swift) bin="/usr/bin/swift" ;;
  swiftlint) bin="/opt/homebrew/bin/swiftlint" ;;
  npm) bin="/opt/homebrew/bin/npm" ;;
  npx) bin="/opt/homebrew/bin/npx" ;;
  yarn) bin="/opt/homebrew/bin/yarn" ;;
  pnpm) bin="/opt/homebrew/bin/pnpm" ;;
  node) bin="/opt/homebrew/bin/node" ;;
  bundle) bin="/opt/homebrew/bin/bundle" ;;
  pod) bin="/opt/homebrew/bin/pod" ;;
  fastlane) bin="/opt/homebrew/bin/fastlane" ;;
  ruby) bin="/usr/bin/ruby" ;;
  gem) bin="/usr/bin/gem" ;;
  python3) bin="/usr/bin/python3" ;;
  plutil) bin="/usr/bin/plutil" ;;
  defaults) bin="/usr/bin/defaults" ;;
  PlistBuddy) bin="/usr/libexec/PlistBuddy" ;;
  ls) bin="/bin/ls" ;;
  pwd) bin="/bin/pwd" ;;
  find) bin="/usr/bin/find" ;;
  rg) bin="/opt/homebrew/bin/rg" ;;
  grep) bin="/usr/bin/grep" ;;
  sed) bin="/usr/bin/sed" ;;
  awk) bin="/usr/bin/awk" ;;
  cat) bin="/bin/cat" ;;
  head) bin="/usr/bin/head" ;;
  tail) bin="/usr/bin/tail" ;;
  bash) bin="/bin/bash" ;;
  sh) bin="/bin/sh" ;;
  rsync) bin="/usr/bin/rsync" ;;
  *) fail "command '$base' not allowed" ;;
esac

for ((i=1; i<${#argv[@]}; i++)); do
  tok="${argv[$i]}"
  [[ "$tok" == "--" ]] && continue
  [[ "$tok" == -* ]] && continue
  [[ "$base" == "rsync" ]] && continue
  [[ "$tok" == *".."* ]] && fail "parent traversal blocked"
  [[ "$tok" == ~* ]] && fail "home expansion blocked"
  if [[ "$tok" == /* && "$tok" != "$ROOT"* ]]; then
    fail "absolute path outside root blocked"
  fi
done

if [[ "$base" == "rsync" ]]; then
  exec "$bin" "${argv[@]:1}"
fi

cd "$ROOT"
exec "$bin" "${argv[@]:1}"
