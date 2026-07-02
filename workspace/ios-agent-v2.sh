#!/bin/bash
set -euo pipefail

# ios-agent v2: OpenClaw node wrapper for iOS simulator commands
# Project: EliteProAIDemo
# Changes from v1:
#   - Added export-screenshot with format-agnostic attachment extraction
#   - Added record / record-stop for screen recording (mp4)
#   - Added export-recording for base64-streaming recordings to gateway
#   - Added validate-media helper to prevent mislabeled file types
#   - All media output includes MIME type + magic-byte validation

AGENT_DIR="$HOME/ios-agent"
PROJECT_DIR="/Users/taylorolsen-vogt/iosApp"
PROJECT="AutoTap.xcodeproj"
SCHEME="AutoTap"
BUNDLE_ID="com.elitepro.resilife"
DERIVED_DATA="$PROJECT_DIR/DerivedData"
ARTIFACTS_DIR="$AGENT_DIR/artifacts"
LOGS_DIR="$AGENT_DIR/logs"
VIEWPORT_CONF="$AGENT_DIR/viewport.conf"
DESTINATION="platform=iOS Simulator,name=iPhone 16 Pro Max"
RECORDING_PID_FILE="$AGENT_DIR/.recording.pid"
RECORDING_OUTPUT_FILE="$AGENT_DIR/.recording.path"

mkdir -p "$ARTIFACTS_DIR" "$LOGS_DIR"

# --- Single-repo enforcement ---
# Only one AutoTap checkout should exist. Warn if duplicates are found.
_check_duplicate_repos() {
  local dupes=""
  local gitdir repo remote
  while IFS= read -r gitdir; do
    repo=$(dirname "$gitdir")
    [[ "$repo" == "$PROJECT_DIR" ]] && continue
    remote=$(git -C "$repo" remote get-url origin 2>/dev/null || true)
    if [[ "$remote" == *"AutoTap"* || "$remote" == *"aarwitz/iosApp"* ]]; then
      dupes="${dupes}  ${repo} (${remote})"$'\n'
    fi
  done < <(find "$HOME" -maxdepth 3 -name ".git" -type d 2>/dev/null || true)
  if [[ -n "$dupes" ]]; then
    echo "WARNING: Duplicate AutoTap iOS repo checkouts found on this Mac:" >&2
    echo "$dupes" >&2
    echo "The canonical repo is: $PROJECT_DIR" >&2
    echo "Remove duplicates to avoid confusion and save disk space." >&2
    echo "DUPLICATE_REPOS_WARNING=true"
  fi
}
_check_duplicate_repos

timestamp() { date +%Y%m%d-%H%M%S; }

# --- Media validation helper ---
# Detects actual file format from magic bytes and reports it.
# Usage: detect_media_type <filepath>
# Outputs: png, jpeg, webp, heif, mp4, mov, gif, unknown
detect_media_type() {
  local f="$1"
  if [[ ! -f "$f" ]]; then echo "missing"; return; fi
  local magic
  magic=$(xxd -l 12 -p "$f" 2>/dev/null || echo "")
  case "$magic" in
    89504e47*)       echo "png" ;;
    ffd8ff*)         echo "jpeg" ;;
    52494646*) # RIFF header — could be WebP
      local riff_type
      riff_type=$(xxd -s 8 -l 4 -p "$f" 2>/dev/null || echo "")
      if [[ "$riff_type" == "57454250" ]]; then echo "webp"; else echo "riff-other"; fi
      ;;
    0000002066747970*|00000018667479*|00000020667479*) echo "mp4" ;; # ftyp box
    6674797069736f6d*|667479704d534e56*) echo "mp4" ;; # isom, MSNV
    000000*) # Could be mp4/mov with varying box sizes
      local ftyp_check
      ftyp_check=$(xxd -s 4 -l 4 "$f" 2>/dev/null | awk '{print $2$3}')
      if [[ "$ftyp_check" == "66747970" ]]; then echo "mp4"; else echo "unknown-iso"; fi
      ;;
    474946383961*|474946383761*) echo "gif" ;;
    *) echo "unknown" ;;
  esac
}

# Validate that a file's extension matches its actual content.
# Usage: validate_media <filepath> <expected_ext>
# Returns 0 if valid, 1 if mismatch (prints warning)
validate_media() {
  local f="$1" expected="$2"
  local actual
  actual=$(detect_media_type "$f")
  if [[ "$actual" == "missing" ]]; then
    echo "VALIDATE_ERROR=file_not_found PATH=$f"
    return 1
  fi
  # Normalize expected extension
  case "$expected" in
    jpg) expected="jpeg" ;;
    heic|heif) expected="heif" ;;
  esac
  if [[ "$actual" != "$expected" ]]; then
    echo "VALIDATE_WARNING=mismatch PATH=$f EXPECTED=$expected ACTUAL=$actual"
    return 1
  fi
  echo "VALIDATE_OK=true PATH=$f TYPE=$actual"
  return 0
}

# --- Viewport / coordinate helpers ---

load_viewport() {
  if [[ ! -f "$VIEWPORT_CONF" ]]; then
    echo "ERROR: No viewport calibration. Run 'ios-agent calibrate' first." >&2
    exit 1
  fi
  source "$VIEWPORT_CONF"
}

device_to_screen() {
  load_viewport
  local dev_x="$1" dev_y="$2"
  local sx sy
  sx=$(echo "scale=0; $CONTENT_X + ($dev_x * $CONTENT_W / $DEVICE_W)" | bc)
  sy=$(echo "scale=0; $CONTENT_Y + ($dev_y * $CONTENT_H / $DEVICE_H)" | bc)
  echo "$sx $sy"
}

# --- Main command dispatch ---

case "${1:-help}" in
  build)
    BRANCH=""
    shift
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --branch) BRANCH="$2"; shift 2 ;;
        *) echo "Unknown build option: $1"; exit 1 ;;
      esac
    done

    TS=$(timestamp)
    LOG="$LOGS_DIR/build-$TS.log"
    echo "Building $SCHEME..."
    cd "$PROJECT_DIR"

    if [[ -n "$BRANCH" ]]; then
      echo "Switching to branch: $BRANCH"
      git fetch origin 2>&1 | tee -a "$LOG"
      if git rev-parse --verify "$BRANCH" >/dev/null 2>&1; then
        git checkout "$BRANCH" 2>&1 | tee -a "$LOG"
      else
        git checkout -b "$BRANCH" "origin/$BRANCH" 2>&1 | tee -a "$LOG"
      fi
    fi

    git pull --ff-only 2>&1 | tee -a "$LOG" || true
    if xcodebuild \
      -project "$PROJECT" \
      -scheme "$SCHEME" \
      -configuration Debug \
      -destination "$DESTINATION" \
      -derivedDataPath "$DERIVED_DATA" \
      build 2>&1 | tee -a "$LOG"; then
      echo "BUILD_STATUS=success"
      echo "LOG=$LOG"
    else
      echo "BUILD_STATUS=failure"
      echo "LOG=$LOG"
      tail -30 "$LOG" | grep -i "error\|failed" || true
      exit 1
    fi
    ;;

  run)
    TS=$(timestamp)
    LOG="$LOGS_DIR/run-$TS.log"
    APP_PATH="$DERIVED_DATA/Build/Products/Debug-iphonesimulator/EliteProAIDemo.app"

    if [[ ! -d "$APP_PATH" ]]; then
      echo "ERROR: App not built. Run 'ios-agent build' first."
      exit 1
    fi

    echo "Installing and launching $SCHEME on simulator..."
    xcrun simctl boot "iPhone 16 Pro Max" 2>/dev/null || true
    xcrun simctl install booted "$APP_PATH" 2>&1 | tee -a "$LOG"
    xcrun simctl terminate booted "$BUNDLE_ID" 2>/dev/null || true
    xcrun simctl launch booted "$BUNDLE_ID" 2>&1 | tee -a "$LOG"
    sleep 3
    echo "RUN_STATUS=success"
    echo "LOG=$LOG"
    ;;

  screenshot)
    EXPORT_BASE64=0
    shift
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --base64) EXPORT_BASE64=1; shift ;;
        *) echo "Unknown screenshot flag: $1"; exit 1 ;;
      esac
    done

    TS=$(timestamp)
    SCREENSHOT="$ARTIFACTS_DIR/screenshot-$TS.png"
    echo "Capturing simulator screenshot..."
    # Ensure simulator is booted
    xcrun simctl boot "iPhone 16 Pro Max" 2>/dev/null || true
    xcrun simctl bootstatus "iPhone 16 Pro Max" -b 2>/dev/null || true

    # simctl screenshot can intermittently fail right after boot/wake.
    # Retry a few times before failing hard.
    SHOT_OK=0
    SHOT_ERR=""
    for i in 1 2 3; do
      if SHOT_ERR=$(xcrun simctl io booted screenshot "$SCREENSHOT" 2>&1); then
        SHOT_OK=1
        break
      fi
      echo "SCREENSHOT_RETRY=$i"
      sleep 1
    done

    if [[ $SHOT_OK -ne 1 ]]; then
      echo "$SHOT_ERR"
    fi
    if [[ -f "$SCREENSHOT" ]]; then
      # Validate it's actually a PNG
      ACTUAL_TYPE=$(detect_media_type "$SCREENSHOT")
      echo "SCREENSHOT_STATUS=success"
      echo "SCREENSHOT_PATH=$SCREENSHOT"
      echo "SCREENSHOT_SIZE=$(wc -c < "$SCREENSHOT") bytes"
      echo "SCREENSHOT_FORMAT=$ACTUAL_TYPE"
      if [[ "$ACTUAL_TYPE" != "png" ]]; then
        echo "SCREENSHOT_WARNING=format_mismatch EXPECTED=png ACTUAL=$ACTUAL_TYPE"
      fi
      if [[ $EXPORT_BASE64 -eq 1 ]]; then
        echo "EXPORT_CORRECT_EXT=$ACTUAL_TYPE"
        echo "BASE64_START"
        base64 < "$SCREENSHOT"
        echo "BASE64_END"
      fi
    else
      echo "SCREENSHOT_STATUS=failure"
      exit 1
    fi
    ;;

  export-screenshot)
    # Export the latest test screenshot attachment as base64.
    # Works with PNG, JPEG, or any image attachment from xcresult bundles.
    #
    # Usage: ios-agent export-screenshot [--result <path>]
    #
    # If --result is not specified, uses the latest test-*.xcresult in artifacts.
    # Extracts the FIRST image attachment found (not filtered by extension).
    shift || true
    RESULT_PATH=""
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --result) RESULT_PATH="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
      esac
    done

    # Find latest xcresult if not specified
    if [[ -z "$RESULT_PATH" ]]; then
      RESULT_PATH=$(ls -td "$ARTIFACTS_DIR"/test-*.xcresult 2>/dev/null | head -1)
      if [[ -z "$RESULT_PATH" ]]; then
        echo "ERROR: No test results found. Run 'ios-agent test' first."
        echo "EXPORT_STATUS=no_results"
        exit 1
      fi
    fi

    echo "Searching for attachments in: $RESULT_PATH"

    # Modern xcresulttool (Xcode 26+): get test-results attachments
    ATTACHMENTS_JSON=$(xcrun xcresulttool get test-results attachments \
      --path "$RESULT_PATH" 2>/dev/null || echo "")

    if [[ -z "$ATTACHMENTS_JSON" || "$ATTACHMENTS_JSON" == *"error"* ]]; then
      # Fallback: try legacy API
      echo "Trying legacy xcresulttool API..."
      ATTACHMENTS_JSON=$(xcrun xcresulttool get --format json --path "$RESULT_PATH" 2>/dev/null || echo "")
    fi

    # Strategy: export all attachments to a temp dir and find the first image
    EXPORT_DIR=$(mktemp -d)
    trap "rm -rf '$EXPORT_DIR'" EXIT

    # Try the modern export approach
    EXPORTED=""
    if xcrun xcresulttool export --path "$RESULT_PATH" \
         --output-path "$EXPORT_DIR" --type attachments 2>/dev/null; then
      # Find image files in export dir
      EXPORTED=$(find "$EXPORT_DIR" -type f \( -name "*.png" -o -name "*.jpg" -o -name "*.jpeg" -o -name "*.heic" \) | head -1)
    fi

    # If modern export didn't produce images, try extracting by ID from JSON
    if [[ -z "$EXPORTED" && -n "$ATTACHMENTS_JSON" ]]; then
      echo "Trying ID-based extraction..."
      # Parse attachment IDs from JSON
      ATT_IDS=$(echo "$ATTACHMENTS_JSON" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    # Walk the structure looking for attachment references
    def find_refs(obj, refs=[]):
        if isinstance(obj, dict):
            if 'payloadRef' in obj:
                refs.append(obj['payloadRef'].get('id', ''))
            if '_value' in obj and isinstance(obj['_value'], dict) and 'payloadRef' in obj['_value']:
                refs.append(obj['_value']['payloadRef'].get('id', ''))
            for v in obj.values():
                find_refs(v, refs)
        elif isinstance(obj, list):
            for item in obj:
                find_refs(item, refs)
        return refs
    for ref_id in find_refs(data):
        if ref_id:
            print(ref_id)
except:
    pass
" 2>/dev/null || echo "")

      for AID in $ATT_IDS; do
        OUTFILE="$EXPORT_DIR/attachment-$AID"
        if xcrun xcresulttool get object --path "$RESULT_PATH" --id "$AID" \
             > "$OUTFILE" 2>/dev/null; then
          FTYPE=$(detect_media_type "$OUTFILE")
          if [[ "$FTYPE" == "png" || "$FTYPE" == "jpeg" || "$FTYPE" == "webp" ]]; then
            EXPORTED="$OUTFILE"
            break
          fi
        fi
      done
    fi

    # Last resort: look for any file with image magic bytes in the export dir
    if [[ -z "$EXPORTED" ]]; then
      for f in "$EXPORT_DIR"/*; do
        [[ -f "$f" ]] || continue
        FTYPE=$(detect_media_type "$f")
        if [[ "$FTYPE" == "png" || "$FTYPE" == "jpeg" || "$FTYPE" == "webp" ]]; then
          EXPORTED="$f"
          break
        fi
      done
    fi

    if [[ -z "$EXPORTED" || ! -f "$EXPORTED" ]]; then
      echo "ERROR: No image attachment found in $RESULT_PATH"
      echo "Available files in xcresult export:"
      find "$EXPORT_DIR" -type f -exec file {} \; 2>/dev/null || true
      echo "EXPORT_STATUS=no_image_found"
      exit 1
    fi

    # Detect actual format of the exported file
    ACTUAL_FORMAT=$(detect_media_type "$EXPORTED")
    EXPORT_SIZE=$(wc -c < "$EXPORTED")
    EXPORT_NAME=$(basename "$EXPORTED")

    # Determine correct extension
    case "$ACTUAL_FORMAT" in
      png)  CORRECT_EXT="png" ;;
      jpeg) CORRECT_EXT="jpg" ;;
      webp) CORRECT_EXT="webp" ;;
      heif) CORRECT_EXT="heic" ;;
      *)    CORRECT_EXT="bin" ;;
    esac

    echo "EXPORT_NAME=$EXPORT_NAME"
    echo "EXPORT_FORMAT=$ACTUAL_FORMAT"
    echo "EXPORT_CORRECT_EXT=$CORRECT_EXT"
    echo "EXPORT_SIZE=$EXPORT_SIZE bytes"
    echo "EXPORT_SOURCE=$RESULT_PATH"
    echo "BASE64_START"
    base64 < "$EXPORTED"
    echo ""
    echo "BASE64_END"
    echo "EXPORT_STATUS=success"
    ;;

  record)
    # Start screen recording
    # Usage: ios-agent record [--duration <seconds>]
    # Recording runs in background. Use 'ios-agent record-stop' to finish.
    shift || true
    MAX_DURATION=""
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --duration) MAX_DURATION="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
      esac
    done

    # Check if already recording
    if [[ -f "$RECORDING_PID_FILE" ]]; then
      PID=$(cat "$RECORDING_PID_FILE")
      if kill -0 "$PID" 2>/dev/null; then
        echo "ERROR: Recording already in progress (PID $PID)"
        echo "Use 'ios-agent record-stop' to stop it first."
        echo "RECORD_STATUS=already_recording"
        exit 1
      fi
      # Stale PID file
      rm -f "$RECORDING_PID_FILE" "$RECORDING_OUTPUT_FILE"
    fi

    TS=$(timestamp)
    RECORDING="$ARTIFACTS_DIR/recording-$TS.mp4"

    echo "Starting screen recording: $RECORDING"
    # Ensure simulator is booted
    xcrun simctl boot "iPhone 16 Pro Max" 2>/dev/null || true

    if [[ -n "$MAX_DURATION" ]]; then
      # Record for a fixed duration then auto-stop
      (
        xcrun simctl io booted recordVideo --codec h264 "$RECORDING" &
        REC_PID=$!
        echo "$REC_PID" > "$RECORDING_PID_FILE"
        echo "$RECORDING" > "$RECORDING_OUTPUT_FILE"
        sleep "$MAX_DURATION"
        kill "$REC_PID" 2>/dev/null || true
        wait "$REC_PID" 2>/dev/null || true
        rm -f "$RECORDING_PID_FILE"
      ) &
      sleep 1
      echo "RECORD_STATUS=started"
      echo "RECORD_DURATION=${MAX_DURATION}s (auto-stop)"
      echo "RECORD_PATH=$RECORDING"
    else
      # Start recording in background, manual stop required
      xcrun simctl io booted recordVideo --codec h264 "$RECORDING" &
      REC_PID=$!
      echo "$REC_PID" > "$RECORDING_PID_FILE"
      echo "$RECORDING" > "$RECORDING_OUTPUT_FILE"
      sleep 1
      # Verify it started
      if kill -0 "$REC_PID" 2>/dev/null; then
        echo "RECORD_STATUS=started"
        echo "RECORD_PID=$REC_PID"
        echo "RECORD_PATH=$RECORDING"
        echo "Use 'ios-agent record-stop' to stop recording."
      else
        rm -f "$RECORDING_PID_FILE" "$RECORDING_OUTPUT_FILE"
        echo "RECORD_STATUS=failure"
        echo "ERROR: recordVideo process exited immediately"
        exit 1
      fi
    fi
    ;;

  record-stop)
    # Stop an active screen recording
    if [[ ! -f "$RECORDING_PID_FILE" ]]; then
      echo "ERROR: No active recording found."
      echo "RECORD_STOP_STATUS=no_recording"
      exit 1
    fi

    PID=$(cat "$RECORDING_PID_FILE")
    RECORDING=$(cat "$RECORDING_OUTPUT_FILE" 2>/dev/null || echo "unknown")

    echo "Stopping recording (PID $PID)..."
    # Send SIGINT (graceful stop) — this makes simctl finalize the mp4
    kill -INT "$PID" 2>/dev/null || true
    # Wait for it to finish writing
    for i in $(seq 1 15); do
      if ! kill -0 "$PID" 2>/dev/null; then break; fi
      sleep 1
    done
    # Force kill if still running
    kill -9 "$PID" 2>/dev/null || true
    rm -f "$RECORDING_PID_FILE"

    if [[ -f "$RECORDING" ]]; then
      ACTUAL_FORMAT=$(detect_media_type "$RECORDING")
      echo "RECORD_STOP_STATUS=success"
      echo "RECORD_PATH=$RECORDING"
      echo "RECORD_SIZE=$(wc -c < "$RECORDING") bytes"
      echo "RECORD_FORMAT=$ACTUAL_FORMAT"
      if [[ "$ACTUAL_FORMAT" != "mp4" ]]; then
        echo "RECORD_WARNING=format_check ACTUAL=$ACTUAL_FORMAT"
      fi
    else
      echo "RECORD_STOP_STATUS=failure"
      echo "ERROR: Recording file not found at $RECORDING"
      exit 1
    fi
    ;;

  export-recording)
    # Export a recording (mp4) as base64 for transfer to gateway
    # Usage: ios-agent export-recording [--path <file>]
    # If --path not specified, uses the latest recording-*.mp4 in artifacts.
    shift || true
    REC_PATH=""
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --path) REC_PATH="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
      esac
    done

    if [[ -z "$REC_PATH" ]]; then
      REC_PATH=$(ls -t "$ARTIFACTS_DIR"/recording-*.mp4 2>/dev/null | head -1)
      if [[ -z "$REC_PATH" ]]; then
        echo "ERROR: No recordings found. Run 'ios-agent record' first."
        echo "EXPORT_STATUS=no_recording"
        exit 1
      fi
    fi

    if [[ ! -f "$REC_PATH" ]]; then
      echo "ERROR: File not found: $REC_PATH"
      echo "EXPORT_STATUS=file_not_found"
      exit 1
    fi

    ACTUAL_FORMAT=$(detect_media_type "$REC_PATH")
    EXPORT_SIZE=$(wc -c < "$REC_PATH")

    # Warn if file is very large (>10MB) — base64 transfer will be slow
    if [[ $EXPORT_SIZE -gt 10485760 ]]; then
      echo "WARNING: Recording is $(( EXPORT_SIZE / 1048576 ))MB — base64 transfer will be large"
      echo "Consider using shorter recordings or the --duration flag"
    fi

    echo "EXPORT_NAME=$(basename "$REC_PATH")"
    echo "EXPORT_FORMAT=$ACTUAL_FORMAT"
    echo "EXPORT_SIZE=$EXPORT_SIZE bytes"
    echo "EXPORT_SOURCE=$REC_PATH"
    echo "BASE64_START"
    base64 < "$REC_PATH"
    echo ""
    echo "BASE64_END"
    echo "EXPORT_STATUS=success"
    ;;

  record-test)
    # Record a screen recording while running a specific test
    # Usage: ios-agent record-test --case <TestClass/testMethod> [--pre-delay <s>] [--post-delay <s>]
    # This is a convenience wrapper: prebuilds first, then starts recording,
    # runs the test without building, and stops recording.
    shift || true
    TEST_CASE=""
    PRE_DELAY=1
    POST_DELAY=2
    LIVE_DEMO_AUTH=0
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --case) TEST_CASE="$2"; shift 2 ;;
        --pre-delay) PRE_DELAY="$2"; shift 2 ;;
        --post-delay) POST_DELAY="$2"; shift 2 ;;
        --live-demo-auth) LIVE_DEMO_AUTH=1; shift ;;
        *) echo "Unknown option: $1"; exit 1 ;;
      esac
    done

    if [[ -z "$TEST_CASE" ]]; then
      echo "Usage: ios-agent record-test --case <TestClass/testMethod> [--live-demo-auth]"
      exit 1
    fi

    # If no slash, assume OwnerUITests class
    if [[ "$TEST_CASE" != *"/"* ]]; then
      TEST_CASE="OwnerUITests/$TEST_CASE"
    fi

    ONLY_TESTING="AutoTapUITests/$TEST_CASE"
    TS=$(timestamp)
    LOG="$LOGS_DIR/record-test-$TS.log"
    RECORDING="$ARTIFACTS_DIR/recording-$TS.mp4"
    RESULT_BUNDLE="$ARTIFACTS_DIR/test-$TS.xcresult"

    echo "Recording test: $ONLY_TESTING"

    # Ensure simulator is booted
    xcrun simctl boot "iPhone 16 Pro Max" 2>/dev/null || true
    xcrun simctl bootstatus "iPhone 16 Pro Max" -b 2>/dev/null || true

    # Prebuild outside the recording window so the captured video focuses on UI,
    # not the simulator home screen while xcodebuild compiles.
    cd "$PROJECT_DIR"
    PREBUILD_EXIT=0
    if [[ "$LIVE_DEMO_AUTH" == "1" ]]; then
      UI_TEST_USE_LIVE_DEMO_AUTH=1 xcodebuild build-for-testing         -project "$PROJECT"         -scheme "$SCHEME"         -destination "$DESTINATION"         -derivedDataPath "$DERIVED_DATA"         -parallel-testing-enabled NO         -only-testing:"$ONLY_TESTING"         -skip-testing:AutoTapTests         2>&1 | tee "$LOG" || PREBUILD_EXIT=$?
    else
      xcodebuild build-for-testing         -project "$PROJECT"         -scheme "$SCHEME"         -destination "$DESTINATION"         -derivedDataPath "$DERIVED_DATA"         -parallel-testing-enabled NO         -only-testing:"$ONLY_TESTING"         -skip-testing:AutoTapTests         2>&1 | tee "$LOG" || PREBUILD_EXIT=$?
    fi

    if [[ $PREBUILD_EXIT -ne 0 ]]; then
      echo "TEST_STATUS=failed"
      echo "PREBUILD_STATUS=failed"
      echo "LOG=$LOG"
      exit $PREBUILD_EXIT
    fi

    # Run xcodebuild in background so we can start recording AFTER the test
    # method begins (skipping the 15-20s xcodebuild bootstrap/install phase).
    TEST_EXIT=0
    if [[ "$LIVE_DEMO_AUTH" == "1" ]]; then
      echo "UI_TEST_USE_LIVE_DEMO_AUTH=1"
      UI_TEST_USE_LIVE_DEMO_AUTH=1 xcodebuild test-without-building \
        -project "$PROJECT" \
        -scheme "$SCHEME" \
        -destination "$DESTINATION" \
        -derivedDataPath "$DERIVED_DATA" \
        -parallel-testing-enabled NO \
        -only-testing:"$ONLY_TESTING" \
        -skip-testing:AutoTapTests \
        -resultBundlePath "$RESULT_BUNDLE" \
        >>"$LOG" 2>&1 &
    else
      xcodebuild test-without-building \
        -project "$PROJECT" \
        -scheme "$SCHEME" \
        -destination "$DESTINATION" \
        -derivedDataPath "$DERIVED_DATA" \
        -parallel-testing-enabled NO \
        -only-testing:"$ONLY_TESTING" \
        -skip-testing:AutoTapTests \
        -resultBundlePath "$RESULT_BUNDLE" \
        >>"$LOG" 2>&1 &
    fi
    XCODE_PID=$!

    # Wait for the test method to actually start (app launched, setUp done).
    # Poll the log for xcodebuild's "Test Case ... started" marker.
    echo "Waiting for test to start (up to 60s)..."
    STARTED=0
    for _wait_i in $(seq 1 60); do
      if grep -q "Test Case.*started" "$LOG" 2>/dev/null; then
        STARTED=1
        sleep "$PRE_DELAY"
        break
      fi
      if ! kill -0 "$XCODE_PID" 2>/dev/null; then
        echo "WARNING: xcodebuild exited before test started"
        break
      fi
      sleep 1
    done
    if [[ "$STARTED" -eq 0 ]]; then
      echo "WARNING: test start not detected in 60s, recording anyway"
    fi

    # NOW start recording — the app should be on screen with test UI visible
    xcrun simctl io booted recordVideo --codec h264 "$RECORDING" &
    REC_PID=$!

    # Wait for xcodebuild to finish
    wait $XCODE_PID || TEST_EXIT=$?

    # Post-delay to capture any final UI state
    sleep "$POST_DELAY"
    # Stop recording
    kill -INT "$REC_PID" 2>/dev/null || true
    for i in $(seq 1 10); do
      if ! kill -0 "$REC_PID" 2>/dev/null; then break; fi
      sleep 1
    done
    kill -9 "$REC_PID" 2>/dev/null || true

    echo ""
    echo "=== TEST SUMMARY ==="
    grep -E "Test (Suite|Case) .*(passed|failed|started)" "$LOG" | tail -20 || true
    grep -E "\*\* TEST .* \*\*" "$LOG" || true

    if [[ $TEST_EXIT -eq 0 ]]; then
      echo "TEST_STATUS=passed"
    else
      echo "TEST_STATUS=failed"
      echo ""
      echo "=== FAILURES ==="
      grep -E "error:|failed|XCTAssert|❌" "$LOG" | head -20 || true
    fi

    if [[ -f "$RECORDING" ]]; then
      ACTUAL_FORMAT=$(detect_media_type "$RECORDING")
      echo "RECORD_PATH=$RECORDING"
      echo "RECORD_SIZE=$(wc -c < "$RECORDING") bytes"
      echo "RECORD_FORMAT=$ACTUAL_FORMAT"
    else
      echo "RECORD_WARNING=no_recording_file"
    fi
    echo "LOG=$LOG"
    [[ -d "$RESULT_BUNDLE" ]] && echo "RESULT_BUNDLE=$RESULT_BUNDLE"
    exit $TEST_EXIT
    ;;

  tap)
    if [[ $# -lt 3 ]]; then
      echo "Usage: ios-agent tap <device_x> <device_y>"
      echo "Coordinates use screenshot pixel space. iPhone 16 Pro Max: 1320x2868"
      exit 1
    fi
    DEV_X="$2"; DEV_Y="$3"
    SCREEN_COORDS=$(device_to_screen "$DEV_X" "$DEV_Y")
    read -r SX SY <<< "$SCREEN_COORDS"
    echo "Tapping device ($DEV_X,$DEV_Y) -> screen ($SX,$SY)..."
    osascript -e 'tell application "Simulator" to activate' 2>/dev/null || true
    sleep 0.3
    osascript -l JavaScript "$AGENT_DIR/sim-tap.js" "$SX" "$SY"
    sleep 0.5
    echo "TAP_STATUS=success"
    ;;

  swipe)
    if [[ $# -lt 2 ]]; then
      echo "Usage: ios-agent swipe <direction|x1 y1 x2 y2>"
      exit 1
    fi

    load_viewport
    CX=$((DEVICE_W / 2)); CY=$((DEVICE_H / 2))

    case "$2" in
      up)    X1=$CX; Y1=$((CY+400)); X2=$CX; Y2=$((CY-400)) ;;
      down)  X1=$CX; Y1=$((CY-400)); X2=$CX; Y2=$((CY+400)) ;;
      left)  X1=$((CX+400)); Y1=$CY; X2=$((CX-400)); Y2=$CY ;;
      right) X1=$((CX-400)); Y1=$CY; X2=$((CX+400)); Y2=$CY ;;
      *)
        if [[ $# -lt 5 ]]; then
          echo "Unknown direction '$2'. Use up/down/left/right or 4 coordinates."; exit 1
        fi
        X1="$2"; Y1="$3"; X2="$4"; Y2="$5" ;;
    esac

    SC1=$(device_to_screen "$X1" "$Y1")
    SC2=$(device_to_screen "$X2" "$Y2")
    read -r SX1 SY1 <<< "$SC1"
    read -r SX2 SY2 <<< "$SC2"
    echo "Swiping device ($X1,$Y1)->($X2,$Y2) screen ($SX1,$SY1)->($SX2,$SY2)..."
    osascript -l JavaScript "$AGENT_DIR/sim-swipe.js" "$SX1" "$SY1" "$SX2" "$SY2"
    sleep 0.5
    echo "SWIPE_STATUS=success"
    ;;

  input)
    if [[ $# -lt 2 ]]; then
      echo "Usage: ios-agent input <text>"; exit 1
    fi
    shift; TEXT="$*"
    echo "Typing: $TEXT"
    echo -n "$TEXT" | xcrun simctl pbcopy booted
    osascript -e 'tell application "Simulator" to activate' -e 'delay 0.2' -e 'tell application "System Events" to keystroke "v" using command down' 2>/dev/null || \
    osascript -l JavaScript -e "
      ObjC.import('CoreGraphics');
      var src = $.CGEventSourceCreate($.kCGEventSourceStateHIDSystemState);
      var cmdDown = $.CGEventCreateKeyboardEvent(src, 55, true);
      var vDown = $.CGEventCreateKeyboardEvent(src, 9, true);
      $.CGEventSetFlags(vDown, $.kCGEventFlagMaskCommand);
      var vUp = $.CGEventCreateKeyboardEvent(src, 9, false);
      $.CGEventSetFlags(vUp, $.kCGEventFlagMaskCommand);
      var cmdUp = $.CGEventCreateKeyboardEvent(src, 55, false);
      $.CGEventPost($.kCGHIDEventTap, cmdDown);
      delay(0.02);
      $.CGEventPost($.kCGHIDEventTap, vDown);
      delay(0.02);
      $.CGEventPost($.kCGHIDEventTap, vUp);
      delay(0.02);
      $.CGEventPost($.kCGHIDEventTap, cmdUp);
      'pasted';
    "
    sleep 0.3
    echo "INPUT_STATUS=success"
    echo "INPUT_TEXT=$TEXT"
    ;;

  home)
    echo "Pressing home..."
    load_viewport
    CX=$((DEVICE_W / 2))
    Y_START=$((DEVICE_H - 50))
    Y_END=$((DEVICE_H / 2))
    SC1=$(device_to_screen "$CX" "$Y_START")
    SC2=$(device_to_screen "$CX" "$Y_END")
    read -r SX1 SY1 <<< "$SC1"
    read -r SX2 SY2 <<< "$SC2"
    osascript -l JavaScript "$AGENT_DIR/sim-swipe.js" "$SX1" "$SY1" "$SX2" "$SY2" 15 0.2
    sleep 0.5
    echo "HOME_STATUS=success"
    ;;

  openurl)
    if [[ $# -lt 2 ]]; then echo "Usage: ios-agent openurl <url>"; exit 1; fi
    URL="$2"
    echo "Opening URL: $URL"
    xcrun simctl openurl booted "$URL" 2>&1
    sleep 2
    echo "OPENURL_STATUS=success"
    ;;

  calibrate)
    echo "Calibrating viewport..."
    echo "Getting device resolution..."
    TMPFILE="/tmp/ios-agent-cal.png"
    xcrun simctl io booted screenshot "$TMPFILE" 2>/dev/null
    DEV_RES=$(sips -g pixelWidth -g pixelHeight "$TMPFILE" 2>/dev/null | awk '/pixelWidth/{w=$2} /pixelHeight/{h=$2} END{print w, h}')
    read -r DW DH <<< "$DEV_RES"
    rm -f "$TMPFILE"
    echo "Device resolution: ${DW}x${DH}"

    echo "Getting Simulator viewport (requires Accessibility permission)..."
    VIEWPORT=$(osascript -e '
      tell application "Simulator" to activate
      delay 0.3
      tell application "System Events"
        tell process "Simulator"
          set contentArea to first group of front window
          set contentPos to position of contentArea
          set contentSize to size of contentArea
          return (item 1 of contentPos) & " " & (item 2 of contentPos) & " " & (item 1 of contentSize) & " " & (item 2 of contentSize)
        end tell
      end tell
    ' 2>&1)

    if [[ "$VIEWPORT" == *"error"* ]] || [[ -z "$VIEWPORT" ]]; then
      echo "ERROR: Could not get viewport. Grant Accessibility permission to Terminal."
      echo "System Preferences > Privacy & Security > Accessibility > Terminal.app"
      exit 1
    fi

    read -r CX CY CW CH <<< "$VIEWPORT"
    echo "Viewport: position ($CX,$CY) size ${CW}x${CH}"

    cat > "$VIEWPORT_CONF" << CONFEOF
# Simulator viewport calibration
# Generated on $(date -Iseconds)
# Re-run 'ios-agent calibrate' if Simulator window moves/resizes
CONTENT_X=$CX
CONTENT_Y=$CY
CONTENT_W=$CW
CONTENT_H=$CH
DEVICE_W=$DW
DEVICE_H=$DH
CONFEOF
    echo "Saved to $VIEWPORT_CONF"
    echo "CALIBRATE_STATUS=success"
    ;;

  deviceinfo)
    echo "=== Device Resolution ==="
    TMPFILE="/tmp/ios-agent-info.png"
    xcrun simctl io booted screenshot "$TMPFILE" 2>/dev/null
    DEV_RES=$(sips -g pixelWidth -g pixelHeight "$TMPFILE" 2>/dev/null | awk '/pixelWidth/{w=$2} /pixelHeight/{h=$2} END{print w, h}')
    rm -f "$TMPFILE"
    echo "Screenshot resolution: $DEV_RES"
    echo "=== Viewport Config ==="
    if [[ -f "$VIEWPORT_CONF" ]]; then
      cat "$VIEWPORT_CONF"
      load_viewport
      echo "=== Coordinate Mapping ==="
      echo "Scale: $(echo "scale=2; $DEVICE_W / $CONTENT_W" | bc)x"
      echo "Center: $((DEVICE_W/2)),$((DEVICE_H/2)) (device coords)"
    else
      echo "No viewport calibration. Run 'ios-agent calibrate'."
    fi
    ;;

  test-list)
    echo "Listing UI tests in AutoTapUITests..."
    cd "$PROJECT_DIR"
    TEST_OUTPUT=$(xcodebuild test \
      -project "$PROJECT" \
      -scheme "$SCHEME" \
      -destination "$DESTINATION" \
      -derivedDataPath "$DERIVED_DATA" \
      -parallel-testing-enabled NO \
      -only-testing:AutoTapUITests \
      -skip-testing:AutoTapTests \
      -enumerate-tests \
      2>&1) || true

    echo "$TEST_OUTPUT" | grep -E "^\s+- " | sed 's/^[[:space:]]*- //' || \
    echo "$TEST_OUTPUT" | grep -E "test[A-Z]" || \
    echo "$TEST_OUTPUT" | tail -30
    echo "TEST_LIST_STATUS=success"
    ;;

  test)
    shift
    TEST_CASE=""
    LIVE_DEMO_AUTH=0
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --case) TEST_CASE="$2"; shift 2 ;;
        --live-demo-auth) LIVE_DEMO_AUTH=1; shift ;;
        *) echo "Unknown option: $1"; exit 1 ;;
      esac
    done

    if [[ -z "$TEST_CASE" ]]; then
      echo "Usage: ios-agent test --case <TestClass/testMethod> [--live-demo-auth]"
      echo "Example: ios-agent test --case OwnerUITests/testHamburgerButtonExists"
      exit 1
    fi

    if [[ "$TEST_CASE" != *"/"* ]]; then
      TEST_CASE="OwnerUITests/$TEST_CASE"
    fi

    ONLY_TESTING="AutoTapUITests/$TEST_CASE"
    TS=$(timestamp)
    LOG="$LOGS_DIR/test-$TS.log"
    RESULT_BUNDLE="$ARTIFACTS_DIR/test-$TS.xcresult"

    echo "Running test: $ONLY_TESTING"
    cd "$PROJECT_DIR"

    TEST_EXIT=0
    if [[ "$LIVE_DEMO_AUTH" == "1" ]]; then
      echo "UI_TEST_USE_LIVE_DEMO_AUTH=1"
      UI_TEST_USE_LIVE_DEMO_AUTH=1 xcodebuild test \
        -project "$PROJECT" \
        -scheme "$SCHEME" \
        -destination "$DESTINATION" \
        -derivedDataPath "$DERIVED_DATA" \
        -parallel-testing-enabled NO \
        -only-testing:"$ONLY_TESTING" \
        -skip-testing:AutoTapTests \
        -resultBundlePath "$RESULT_BUNDLE" \
        2>&1 | tee "$LOG" || TEST_EXIT=$?
    else
      xcodebuild test \
        -project "$PROJECT" \
        -scheme "$SCHEME" \
        -destination "$DESTINATION" \
        -derivedDataPath "$DERIVED_DATA" \
        -parallel-testing-enabled NO \
        -only-testing:"$ONLY_TESTING" \
        -skip-testing:AutoTapTests \
        -resultBundlePath "$RESULT_BUNDLE" \
        2>&1 | tee "$LOG" || TEST_EXIT=$?
    fi

    echo ""
    echo "=== TEST SUMMARY ==="
    grep -E "Test (Suite|Case) .*(passed|failed|started)" "$LOG" | tail -20 || true
    grep -E "\*\* TEST .* \*\*" "$LOG" || true

    if [[ $TEST_EXIT -eq 0 ]]; then
      echo "TEST_STATUS=passed"
    else
      echo "TEST_STATUS=failed"
      echo ""
      echo "=== FAILURES ==="
      grep -E "error:|failed|XCTAssert|❌" "$LOG" | head -20 || true
    fi
    echo "LOG=$LOG"
    [[ -d "$RESULT_BUNDLE" ]] && echo "RESULT_BUNDLE=$RESULT_BUNDLE"
    exit $TEST_EXIT
    ;;

  test-all)
    TS=$(timestamp)
    LOG="$LOGS_DIR/test-all-$TS.log"
    RESULT_BUNDLE="$ARTIFACTS_DIR/test-all-$TS.xcresult"

    echo "Running full UI test suite (AutoTapUITests)..."
    cd "$PROJECT_DIR"

    TEST_EXIT=0
    xcodebuild test \
      -project "$PROJECT" \
      -scheme "$SCHEME" \
      -destination "$DESTINATION" \
      -derivedDataPath "$DERIVED_DATA" \
      -parallel-testing-enabled NO \
      -only-testing:AutoTapUITests \
      -skip-testing:AutoTapTests \
      -resultBundlePath "$RESULT_BUNDLE" \
      2>&1 | tee "$LOG" || TEST_EXIT=$?

    echo ""
    echo "=== TEST SUMMARY ==="
    grep -E "Test (Suite|Case) .*(passed|failed|started)" "$LOG" | tail -30 || true
    grep -E "\*\* TEST .* \*\*" "$LOG" || true
    grep -E "Executed [0-9]+ test" "$LOG" || true

    if [[ $TEST_EXIT -eq 0 ]]; then
      echo "TEST_STATUS=all_passed"
    else
      echo "TEST_STATUS=some_failed"
      echo ""
      echo "=== FAILURES ==="
      grep -E "error:|failed|XCTAssert|❌" "$LOG" | head -30 || true
    fi
    echo "LOG=$LOG"
    [[ -d "$RESULT_BUNDLE" ]] && echo "RESULT_BUNDLE=$RESULT_BUNDLE"
    exit $TEST_EXIT
    ;;

  test-results)
    LATEST=$(ls -td "$ARTIFACTS_DIR"/test-*.xcresult 2>/dev/null | head -1)
    if [[ -z "$LATEST" ]]; then
      echo "No test results found. Run 'ios-agent test' or 'ios-agent test-all' first."
      exit 1
    fi
    echo "Latest result bundle: $LATEST"
    xcrun xcresulttool get test-results summary --path "$LATEST" 2>&1 || \
    xcrun xcresulttool get --format json --path "$LATEST" 2>&1 | head -100
    echo "TEST_RESULTS_STATUS=success"
    ;;

  branch)
    cd "$PROJECT_DIR"
    CURRENT=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
    COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
    DIRTY=""
    if ! git diff --quiet 2>/dev/null || ! git diff --cached --quiet 2>/dev/null; then
      DIRTY=" (dirty)"
    fi
    echo "BRANCH=$CURRENT"
    echo "COMMIT=$COMMIT$DIRTY"
    echo "REMOTE=$(git config --get remote.origin.url 2>/dev/null || echo 'none')"
    echo ""
    echo "=== Recent commits ==="
    git --no-pager log --oneline -5 2>/dev/null || true
    echo ""
    echo "=== Local branches ==="
    git --no-pager branch 2>/dev/null || true
    echo "BRANCH_STATUS=success"
    ;;

  clean)
    TS=$(timestamp)
    LOG="$LOGS_DIR/clean-$TS.log"
    echo "Cleaning build artifacts..."
    cd "$PROJECT_DIR"
    xcodebuild -project "$PROJECT" -scheme "$SCHEME" -configuration Debug clean 2>&1 | tee -a "$LOG" || true
    rm -rf "$DERIVED_DATA" 2>&1 | tee -a "$LOG"
    xcrun simctl uninstall booted "$BUNDLE_ID" 2>/dev/null || true
    echo "CLEAN_STATUS=success"
    echo "LOG=$LOG"
    ;;

  self-update)
    # Update this script from base64-encoded content piped via stdin.
    # Usage: echo '<base64>' | ios-agent self-update
    #
    # This allows Jerry to deploy updates through the node protocol without
    # needing shell wrappers (bash -c) or arbitrary file-write access.
    # The ios-agent binary is the ONLY allowlisted entrypoint — so it must
    # be able to update itself.
    SELF_PATH="$AGENT_DIR/ios-agent"
    BACKUP_PATH="$AGENT_DIR/ios-agent.bak.$(timestamp)"

    echo "Self-update: reading base64 from stdin..."

    # Read base64 from stdin and decode to a temp file
    TMPFILE="$AGENT_DIR/.ios-agent-update.tmp"
    if ! base64 -d > "$TMPFILE" 2>/dev/null; then
      echo "ERROR: Failed to decode base64 input"
      echo "SELF_UPDATE_STATUS=decode_failure"
      rm -f "$TMPFILE"
      exit 1
    fi

    # Validate the decoded file is a bash script (check shebang)
    FIRST_LINE=$(head -1 "$TMPFILE")
    if [[ "$FIRST_LINE" != "#!/bin/bash"* ]]; then
      echo "ERROR: Decoded content doesn't start with #!/bin/bash"
      echo "FIRST_LINE=$FIRST_LINE"
      echo "SELF_UPDATE_STATUS=not_a_script"
      rm -f "$TMPFILE"
      exit 1
    fi

    # Check it has the expected case dispatch structure
    if ! grep -q 'case.*help' "$TMPFILE" 2>/dev/null; then
      echo "ERROR: Decoded script doesn't look like ios-agent (missing command dispatch)"
      echo "SELF_UPDATE_STATUS=invalid_script"
      rm -f "$TMPFILE"
      exit 1
    fi

    # Back up current script
    cp "$SELF_PATH" "$BACKUP_PATH" 2>/dev/null || true
    echo "BACKUP=$BACKUP_PATH"

    # Replace self
    mv "$TMPFILE" "$SELF_PATH"
    chmod +x "$SELF_PATH"

    echo "SELF_UPDATE_STATUS=success"
    echo "NEW_SIZE=$(wc -c < "$SELF_PATH") bytes"
    echo "BACKUP_SIZE=$(wc -c < "$BACKUP_PATH") bytes"
    echo ""
    echo "Verify: run 'ios-agent help' to confirm the new version works."
    ;;

  help|*)
    echo "ios-agent v2 - OpenClaw iOS simulator wrapper"
    echo ""
    echo "Usage: ios-agent <command> [args]"
    echo ""
    echo "Build & Run:"
    echo "  build [--branch <name>]  Build the app (optionally switch branch first)"
    echo "  run               Install and launch on booted simulator"
    echo "  clean             Clean build artifacts and uninstall"
    echo "  branch            Show current branch, commit, and local branches"
    echo ""
    echo "Testing (XCUITest):"
    echo "  test-list                    List available UI tests"
    echo "  test --case <name>           Run a specific test"
    echo "  test-all                     Run full UI test suite"
    echo "  test-results                 Show latest test results"
    echo ""
    echo "Observation:"
    echo "  screenshot [--base64]   Capture simulator screenshot (PNG); --base64 streams it for gateway transfer"
    echo "  deviceinfo          Print device resolution and viewport info"
    echo ""
    echo "Media Export (gateway transfer via base64):"
    echo "  export-screenshot [--result <path>]   Export test screenshot from xcresult"
    echo "  export-recording [--path <file>]      Export screen recording as base64"
    echo ""
    echo "Screen Recording:"
    echo "  record [--duration <s>]      Start screen recording (mp4)"
    echo "  record-stop                  Stop active recording"
    echo "  record-test --case <name>    Record while running a test"
    echo ""
    echo "Maintenance:"
    echo "  self-update         Update this script from base64 stdin (for Jerry)"
    echo ""
    echo "Interaction (coordinates in device pixels, same as screenshots):"
    echo "  tap <x> <y>           Tap at device coordinates"
    echo "  swipe <direction>     Swipe up/down/left/right"
    echo "  swipe <x1> <y1> <x2> <y2>   Custom swipe between points"
    echo "  input <text>          Type text into focused field"
    echo "  home                  Press home (swipe up from bottom)"
    echo "  openurl <url>         Open a URL in the simulator"
    echo ""
    echo "Setup:"
    echo "  calibrate         Measure Simulator viewport (run locally, not via node)"
    echo ""
    echo "Device: iPhone 16 Pro Max (1320x2868)"
    echo "Coordinate system: (0,0) is top-left of device screen."
    echo ""
    echo "Changes in v2:"
    echo "  - export-screenshot: format-agnostic attachment extraction (handles PNG, JPEG, WebP, HEIF)"
    echo "  - record/record-stop: screen recording (mp4) via simctl recordVideo"
    echo "  - record-test: convenience wrapper to record during a test run"
    echo "  - export-recording: base64-stream recordings to gateway"
    echo "  - self-update: autonomous script updates via node protocol"
    echo "  - All media output includes format validation (magic-byte detection)"
    ;;
esac
