#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -euo pipefail

# ewag-test.sh — Run any XCUITest on the Mac node deterministically (no LLM)
#
# Telegram usage:
#   /bash ~/.openclaw/scripts/ewag-test.sh <TestClass/testMethod>
#   /bash ~/.openclaw/scripts/ewag-test.sh list
#   /bash ~/.openclaw/scripts/ewag-test.sh smoke
#   /bash ~/.openclaw/scripts/ewag-test.sh all
#
# Examples:
#   /bash ~/.openclaw/scripts/ewag-test.sh ResidentUITests/testHomeScreenshot
#   /bash ~/.openclaw/scripts/ewag-test.sh OwnerUITests/testOwnerDashboardScreenshot
#   /bash ~/.openclaw/scripts/ewag-test.sh ResidentInteractionTests/testCoachingCarouselSwipe

NODE_NAME="ios-build-node"
MAC_USER="${EWAG_MAC_USER:-taylorolsen-vogt}"
SSH_HOST="${EWAG_MAC_SSH_HOST:-${MAC_USER}@100.125.133.123}"
SSH_OPTS="-o BatchMode=yes -o ConnectTimeout=15"
IOS_AGENT_BIN="${EWAG_IOS_AGENT_BIN:-/Users/${MAC_USER}/ios-agent/ios-agent}"
TIMEOUT="${EWAG_NODE_TIMEOUT_MS:-180}"
RETRY_LIMIT="${EWAG_TEST_RETRY_LIMIT:-2}"
SIMULATOR_NAME="${EWAG_SIMULATOR_NAME:-iPhone 16 Pro Max}"

LIVE_DEMO_AUTH=0
if [[ "${1:-}" == "--live-demo-auth" ]]; then
  LIVE_DEMO_AUTH=1
  shift
fi

run_node_command() {
  ssh $SSH_OPTS -o ServerAliveInterval=15 -o ServerAliveCountMax=20 "$SSH_HOST" "$*" 2>&1
}

recover_simulator() {
  echo "Attempting simulator recovery on node..."
  ssh $SSH_OPTS "$SSH_HOST" "xcrun simctl shutdown all >/dev/null 2>&1 || true; xcrun simctl boot \"${SIMULATOR_NAME}\" >/dev/null 2>&1 || true; xcrun simctl bootstatus \"${SIMULATOR_NAME}\" -b >/dev/null 2>&1 || true" >/dev/null || true
}

looks_like_crash_or_hang() {
  local text="$1"
  echo "$text" | grep -Eqi 'EXC_BAD_ACCESS|Segmentation fault|Termination Reason:  Namespace SIGNAL|TEST_STATUS=failed|timedOut":true|timed out|connection (closed|reset)|xcodebuild.*failed|Early unexpected exit'
}

run_test_case_with_retry() {
  local test_case="$1"
  local extra_flag=""
  if [[ "$LIVE_DEMO_AUTH" == "1" ]]; then
    extra_flag="--live-demo-auth"
  fi
  local repo_flags=""
  if [[ -n "${EWAG_GIT_BRANCH:-}" ]]; then
    repo_flags+=" --branch ${EWAG_GIT_BRANCH}"
  fi
  if [[ -n "${EWAG_GIT_COMMIT:-}" ]]; then
    repo_flags+=" --commit ${EWAG_GIT_COMMIT}"
  fi

  local attempt=1
  local output=""
  while [[ "$attempt" -le "$RETRY_LIMIT" ]]; do
    echo "Attempt $attempt/$RETRY_LIMIT: $test_case"
    set +e
    if [[ -n "$extra_flag" ]]; then
      output="$(run_node_command "$IOS_AGENT_BIN" test "$extra_flag" $repo_flags --case "$test_case")"
    else
      output="$(run_node_command "$IOS_AGENT_BIN" test $repo_flags --case "$test_case")"
    fi
    local rc=$?
    set -e

    echo "$output" | grep -E "Test Case|TEST_STATUS|passed|failed|error:|RESULT_BUNDLE|EXC_BAD_ACCESS|Segmentation|timed out|Timed out" | tail -20 || true

    if echo "$output" | grep -q "TEST_STATUS=passed"; then
      echo "RESULT: PASSED"
      return 0
    fi

    if [[ "$rc" -ne 0 ]]; then
      echo "Node command exited with code $rc"
    fi

    if [[ "$attempt" -lt "$RETRY_LIMIT" ]] && looks_like_crash_or_hang "$output"; then
      echo "Detected crash/hang signature. Retrying after simulator recovery..."
      recover_simulator
      attempt=$((attempt + 1))
      continue
    fi

    echo "RESULT: FAILED"
    echo "$output" | grep -E "error:|EXC_BAD_ACCESS|Segmentation|Termination Reason|timed out|Timed out|TEST_STATUS" | tail -12 || true
    return 1
  done

  echo "RESULT: FAILED (retries exhausted)"
  return 1
}

case "${1:-}" in
  ""|help|-h|--help)
    echo "ewag-test.sh — Run XCUITests from Telegram"
    echo ""
    echo "Commands:"
    echo "  list                          List all available UI tests"
    echo "  smoke                         Run resident + owner smoke screenshots"
    echo "  all                           Run entire UI test suite"
    echo "  --live-demo-auth              Enable credentialed live-demo auth mode"
    echo "  <TestClass/testMethod>        Run a specific test"
    echo ""
    echo "Test classes:"
    echo "  ResidentUITests       — Resident app screenshots & UI checks"
    echo "  OwnerUITests          — Owner dashboard screenshots & navigation"
    echo "  ResidentInteractionTests — Tap, swipe, interaction tests"
    echo ""
    echo "Quick picks:"
    echo "  ResidentUITests/testHomeScreenshot"
    echo "  ResidentUITests/testCoachingScreenshot"
    echo "  ResidentUITests/testNutritionScreenshot"
    echo "  ResidentUITests/testCommunityScreenshot"
    echo "  ResidentUITests/testRewardsScreenshot"
    echo "  ResidentUITests/testAllTabsScreenshot"
    echo "  OwnerUITests/testOwnerDashboardScreenshot"
    echo "  OwnerUITests/testOwnerAllTabsScreenshot"
    echo "  OwnerUITests/testOwnerSideMenuScreenshot"
    exit 0
    ;;

  list)
    echo "Fetching test list from Mac node..."
    run_node_command "$IOS_AGENT_BIN" test-list \
      | grep -E "^(func test|test[A-Z]|  - |ResidentUITests|OwnerUITests|ResidentInteraction)" \
      || echo "(raw output — check Mac logs)"
    ;;

  smoke)
    echo "Running smoke tests..."
    TESTS=(
      "ResidentUITests/testResidentSmokeScreenshot"
      "OwnerUITests/testOwnerSmokeScreenshot"
    )
    PASS=0 FAIL=0
    for t in "${TESTS[@]}"; do
      echo ""
      echo "--- $t ---"
      set +e
      if [[ "$LIVE_DEMO_AUTH" == "1" ]]; then
        OUTPUT="$(run_node_command "$IOS_AGENT_BIN" test --live-demo-auth ${EWAG_GIT_BRANCH:+--branch "$EWAG_GIT_BRANCH"} ${EWAG_GIT_COMMIT:+--commit "$EWAG_GIT_COMMIT"} --case "$t")"
      else
        OUTPUT="$(run_node_command "$IOS_AGENT_BIN" test ${EWAG_GIT_BRANCH:+--branch "$EWAG_GIT_BRANCH"} ${EWAG_GIT_COMMIT:+--commit "$EWAG_GIT_COMMIT"} --case "$t")"
      fi
      set -e
      if echo "$OUTPUT" | grep -q "TEST_STATUS=passed"; then
        echo "PASSED"
        PASS=$((PASS + 1))
      else
        echo "FAILED"
        echo "$OUTPUT" | sed 's/\\n/\n/g' | grep -E "error:|FAIL|TEST_STATUS" | tail -5
        FAIL=$((FAIL + 1))
      fi
    done
    echo ""
    echo "=== SMOKE RESULTS: $PASS passed, $FAIL failed ==="
    ;;

  all)
    echo "Running full UI test suite (this takes several minutes)..."
    if [[ "$LIVE_DEMO_AUTH" == "1" ]]; then
      run_node_command "$IOS_AGENT_BIN" test-all ${EWAG_GIT_BRANCH:+--branch "$EWAG_GIT_BRANCH"} ${EWAG_GIT_COMMIT:+--commit "$EWAG_GIT_COMMIT"} --live-demo-auth \
        | grep -E "REPO_SYNC|TEST SUMMARY|TEST_STATUS|passed|failed|Test (Suite|Case)" \
        | tail -40
    else
      run_node_command "$IOS_AGENT_BIN" test-all ${EWAG_GIT_BRANCH:+--branch "$EWAG_GIT_BRANCH"} ${EWAG_GIT_COMMIT:+--commit "$EWAG_GIT_COMMIT"} \
        | grep -E "REPO_SYNC|TEST SUMMARY|TEST_STATUS|passed|failed|Test (Suite|Case)" \
        | tail -40
    fi
    ;;

  *)
    TEST_CASE="$1"
    echo "Running test: $TEST_CASE"
    run_test_case_with_retry "$TEST_CASE"
    ;;
esac
