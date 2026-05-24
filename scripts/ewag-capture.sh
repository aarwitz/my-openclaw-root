#!/usr/bin/env bash
set -euo pipefail

# ewag-capture.sh — LLM-free screenshot/video capture → Google Drive upload
#
# Usage:
#   ewag-capture.sh <view> [screenshot|record|scroll] [options]
#
# Views: home, coaching, nutrition, community, rewards, profile, challenges,
#        connector, calendar, notifications, bookmarks, messages, settings,
#        spa, pool, all
# Modes: screenshot (default), record, scroll
#   scroll — records a slow top-to-bottom walkthrough (home/coaching/community/rewards supported)
#
# Options:
#   --build              Build current branch before capture
#   --branch <name>      Switch branch, build, then capture
#   --no-upload          Skip Google Drive upload (local capture only)
#   --clean              Clean DerivedData before building
#
# Examples from Telegram:
#   /bash ~/.openclaw/scripts/ewag-capture.sh home
#   /bash ~/.openclaw/scripts/ewag-capture.sh all --build
#   /bash ~/.openclaw/scripts/ewag-capture.sh coaching --branch feature/x
#   /bash ~/.openclaw/scripts/ewag-capture.sh rewards record --no-upload
#   /bash ~/.openclaw/scripts/ewag-capture.sh home scroll
#   /bash ~/.openclaw/scripts/ewag-capture.sh coaching scroll
#   /bash ~/.openclaw/scripts/ewag-capture.sh all --build --clean

MEDIA_DIR="/home/aaron/.openclaw/media/inbound"
NODE_NAME="ios-build-node"
# EWAG execution Mac: Taylor's Mac on Tailscale.
MAC_USER="${EWAG_MAC_USER:-taylorolsen-vogt}"
SSH_HOST="${EWAG_MAC_SSH_HOST:-${MAC_USER}@100.125.133.123}"
SSH_OPTS="-o BatchMode=yes -o ConnectTimeout=15"
IOS_AGENT_BIN="${EWAG_IOS_AGENT_BIN:-/Users/${MAC_USER}/ios-agent/ios-agent}"
IOS_AGENT_ARTIFACTS_DIR="${EWAG_IOS_AGENT_ARTIFACTS_DIR:-/Users/${MAC_USER}/ios-agent/artifacts}"
DATE_STAMP="$(date +%Y-%m-%d)"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
# Trim login/app boot lead-in from scroll recordings.
SCROLL_TRIM_SECONDS="${EWAG_SCROLL_TRIM_SECONDS:-14}"

# Run a command on the Mac node via SSH.
run_on_node() {
  ssh $SSH_OPTS -o ServerAliveInterval=15 -o ServerAliveCountMax=20 "$SSH_HOST" "$*" 2>&1
}

declare -A VIEW_TEST=(
  [home]="ResidentUITests/testHomeScreenshot"
  [coaching]="ResidentUITests/testCoachingScreenshot"
  [nutrition]="ResidentUITests/testNutritionScreenshot"
  [community]="ResidentUITests/testCommunityScreenshot"
  [community-clubs]="ResidentUITests/testClubsTabScreenshot"
  [rewards]="ResidentUITests/testRewardsScreenshot"
  [profile]="ResidentUITests/testProfileScreenshot"
  [challenges]="ResidentUITests/testChallengesScreenshot"
  [connector]="ResidentUITests/testConnectorScreenshot"
  [calendar]="ResidentUITests/testCalendarScreenshot"
  [notifications]="ResidentUITests/testNotificationsScreenshot"
  [bookmarks]="ResidentUITests/testBookmarksScreenshot"
  [messages]="ResidentUITests/testMessagesScreenshot"
  [settings]="ResidentUITests/testSettingsScreenshot"
  [spa]="ResidentUITests/testSpaScreenshot"
  [pool]="ResidentUITests/testPoolScreenshot"
)

# Scroll-recording test cases (slow top-to-bottom scroll, uploaded as WebM)
declare -A VIEW_SCROLL_TEST=(
  [home]="ResidentUITests/testHomeScrollThrough"
  [coaching]="ResidentUITests/testCoachingScrollThrough"
  [community]="ResidentUITests/testCommunityScrollThrough"
  [rewards]="ResidentUITests/testRewardsScrollThrough"
)

declare -A VIEW_DRIVE_FOLDER=(
  [home]="1yP0DcfeCBlhP8vh3IkfZoZPGcHgb8F2K"
  [coaching]="1GzXle3mOIc_N4DqzHnXgLX0Iu5OrP4Tf"
  [nutrition]="1i6N7dADbzS_HmnKS465HMYAhVW0nGlcs"
  [community]="1gSDBT1LFWRM_FU8QICV9GBe0Z39dDtRh"
  [community-clubs]="1gSDBT1LFWRM_FU8QICV9GBe0Z39dDtRh"
  [rewards]="13Ud5sLZqgGWhHxVzqw-juxJiiTD6pbiH"
  [profile]="1HmaXP0Su_tw1iW5AGzwCbkCQxARydyQw"
  [challenges]="1RErODLBhd5sSZH_yUrBX5FNkkFPKs6Cz"
  [connector]="14t72WBeZZirqgfMcZ2lkgCiYHDBykNLM"
  [calendar]="1p3i4dNL6hv5mYuWMGtbGvvEPKboF6y0I"
  [notifications]="1oz1120HAQxXbRXeofZ81VOGpqSuoRF_7"
  [bookmarks]="14ka69RawnnYHbpIeZK6DlEOliyUPpFOi"
  [messages]="1lNdzEV2c4FyMdpVTiD1E8sfanyEXgv7X"
  [settings]="1slKSGq5YthXYciL0pq6lfUnwt92quB5K"
  [spa]="12H7hJPPHTXSSrpkLvf6Pr33-PUU7EvIL"
  [pool]="1conYdCrJd4LJeg9nDp4Vyztz_LZ4D99X"
)

declare -A VIEW_DRIVE_NAME=(
  [home]="Home"
  [coaching]="Coaching"
  [nutrition]="Nutrition"
  [community]="Community"
  [community-clubs]="Community"
  [rewards]="Rewards"
  [profile]="Profile"
  [challenges]="Challenges"
  [connector]="Connector"
  [calendar]="Calendar"
  [notifications]="Notifications"
  [bookmarks]="Bookmarks"
  [messages]="Messages"
  [settings]="Settings"
  [spa]="Spa"
  [pool]="Pool"
)

RECORDINGS_FOLDER="1eHsFkQdD2bfO2pj_ckW32zBpw-zqyd_c"
DRIVE_ROOT_LINK="https://drive.google.com/drive/folders/1rQIIj8sgbrnUhGP1MKzSmsMdHH4xfDDH"

# --- Argument parsing (supports flags in any order) ---
VIEW=""
MODE="screenshot"
DO_BUILD=0
BUILD_BRANCH=""
NO_UPLOAD=0
DO_CLEAN=0
DO_PRODUCTION=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --build)      DO_BUILD=1; shift ;;
    --branch)     DO_BUILD=1; BUILD_BRANCH="${2:?'--branch requires a branch name'}"; shift 2 ;;
    --no-upload)  NO_UPLOAD=1; shift ;;
    --clean)      DO_CLEAN=1; shift ;;
    --production) DO_PRODUCTION=1; shift ;;
    screenshot|record|scroll) MODE="$1"; shift ;;
    -h|--help|help)
      echo "Usage: ewag-capture.sh <view> [screenshot|record|scroll] [options]"
      echo ""
      echo "Views: home, coaching, nutrition, community, community-clubs, rewards, profile,"
      echo "       challenges, connector, calendar, notifications, bookmarks,"
      echo "       messages, settings, spa, pool, all"
      echo "Modes: screenshot (default), record, scroll"
      echo "  scroll — records a slow top-to-bottom walkthrough (home/coaching/community/rewards supported)"
      echo ""
      echo "Options:"
      echo "  --build              Build current branch before capture"
      echo "  --branch <name>      Switch branch, build, then capture"
      echo "  --no-upload          Skip Google Drive upload (local capture only)"
      echo "  --clean              Clean DerivedData before building"
      echo "  --production         Target Railway production backend (no seed, real demo account)"
      echo ""
      echo "Examples:"
      echo "  ewag-capture.sh home                         # Screenshot home, upload to Drive"
      echo "  ewag-capture.sh all --build                  # Build, then screenshot all tabs"
      echo "  ewag-capture.sh coaching --branch feature/x  # Switch branch, build, screenshot"
      echo "  ewag-capture.sh rewards record --no-upload   # Record rewards locally"
      echo "  ewag-capture.sh home scroll                  # Record slow scroll through home"
      echo "  ewag-capture.sh coaching scroll              # Record slow scroll through coaching"
      echo "  ewag-capture.sh all --build --clean          # Clean + build + capture all"
      echo "  ewag-capture.sh home --production            # Screenshot against Railway backend"
      echo "  ewag-capture.sh all --production --no-upload # Capture all from Railway, local only"
      exit 0
      ;;
    *)
      if [[ -z "$VIEW" ]]; then
        VIEW="$(echo "$1" | tr '[:upper:]' '[:lower:]')"
      else
        echo "ERROR: unexpected argument '$1'"
        exit 1
      fi
      shift
      ;;
  esac
done

if [[ -z "$VIEW" ]]; then
  echo "ERROR: view is required. Valid: home, coaching, nutrition, community, community-clubs, rewards, profile, challenges, connector, calendar, notifications, bookmarks, messages, settings, spa, pool, all"
  echo "Run: ewag-capture.sh --help"
  exit 1
fi

if [[ "$MODE" != "screenshot" && "$MODE" != "record" && "$MODE" != "scroll" ]]; then
  echo "ERROR: mode must be 'screenshot', 'record', or 'scroll', got '$MODE'"
  exit 1
fi

if [[ "$VIEW" == "all" ]]; then
  VIEWS=(home coaching nutrition community rewards profile challenges connector calendar notifications bookmarks messages settings spa pool)
else
  if [[ -z "${VIEW_TEST[$VIEW]+x}" ]]; then
    echo "ERROR: unknown view '$VIEW'. Valid: home, coaching, nutrition, community, community-clubs, rewards, profile, challenges, connector, calendar, notifications, bookmarks, messages, settings, spa, pool, all"
    exit 1
  fi
  VIEWS=("$VIEW")
fi

# scroll mode: validate that each requested view has a scroll test defined
if [[ "$MODE" == "scroll" ]]; then
  for v in "${VIEWS[@]}"; do
    if [[ -z "${VIEW_SCROLL_TEST[$v]+x}" ]]; then
      echo "ERROR: scroll mode is not supported for view '$v'."
      echo "Views with scroll support: ${!VIEW_SCROLL_TEST[*]}"
      exit 1
    fi
  done
fi

mkdir -p "$MEDIA_DIR"

extract_marker_value() {
  local marker="$1"
  local text="$2"
  printf '%s' "$text" \
    | sed "s/\\\\n/\n/g; s/\\\\\\\\n/\n/g" \
    | grep -oE "${marker}=[^[:space:]\"',}]+" \
    | head -1 \
    | cut -d= -f2-
}

require_drive_access() {
  if ! gog auth list --check --no-input >/dev/null 2>&1; then
    echo "ERROR: gog Google auth is not healthy."
    echo "Run: gog auth manage --services drive"
    exit 1
  fi
  if ! gog drive search 'owner:me' --max 1 --no-input >/dev/null 2>&1; then
    echo "ERROR: gog Drive access is not ready."
    echo "Run: gog auth manage --services drive"
    exit 1
  fi
}

require_ffmpeg_for_recordings() {
  if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "ERROR: ffmpeg is required for recording conversion (.mp4 -> .webm) but was not found."
    echo "Install ffmpeg on this host, then retry."
    exit 1
  fi
}

convert_mp4_to_webm() {
  local input_mp4="$1"
  local output_webm="$2"
  local trim_start_seconds="${3:-0}"

  local -a trim_args=()
  if [[ "$trim_start_seconds" =~ ^[0-9]+$ ]] && [[ "$trim_start_seconds" -gt 0 ]]; then
    trim_args=(-ss "$trim_start_seconds")
  fi

  ffmpeg -hide_banner -loglevel error -y \
    "${trim_args[@]}" \
    -i "$input_mp4" \
    -c:v libvpx-vp9 -crf 36 -b:v 0 -row-mt 1 \
    -an \
    "$output_webm"
}

declare -a RESULTS=()
declare -A BACKEND_DRIVE_FOLDER_CACHE=()

ensure_drive_subfolder() {
  local parent_folder_id="$1"
  local folder_name="$2"
  local cache_key="${parent_folder_id}:${folder_name}"

  if [[ -n "${BACKEND_DRIVE_FOLDER_CACHE[$cache_key]+x}" ]]; then
    echo "${BACKEND_DRIVE_FOLDER_CACHE[$cache_key]}"
    return 0
  fi

  local list_json
  list_json=$(gog drive ls \
    --parent "$parent_folder_id" \
    --query "mimeType='application/vnd.google-apps.folder' and name='${folder_name}' and trashed=false" \
    --max 50 \
    --json --no-input 2>/dev/null || true)

  local subfolder_id
  subfolder_id=$(printf '%s' "$list_json" | python3 -c "import sys,json; d=json.load(sys.stdin); files=d.get('files') or []; print((files[0].get('id') if files else ''))" 2>/dev/null || true)

  if [[ -z "$subfolder_id" ]]; then
    local mkdir_json
    mkdir_json=$(gog drive mkdir "$folder_name" --parent "$parent_folder_id" --json --no-input 2>&1) || {
      echo ""
      return 1
    }
    subfolder_id=$(printf '%s' "$mkdir_json" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('folder',{}).get('id','') or d.get('file',{}).get('id','') or d.get('id',''))" 2>/dev/null || true)
  fi

  if [[ -z "$subfolder_id" ]]; then
    echo ""
    return 1
  fi

  BACKEND_DRIVE_FOLDER_CACHE[$cache_key]="$subfolder_id"
  echo "$subfolder_id"
}

capture_view() {
  local view="$1"
  local test_case="${VIEW_TEST[$view]}"
  if [[ "$DO_PRODUCTION" -eq 1 && "$MODE" == "screenshot" ]]; then
    test_case="${test_case}_Production"
  fi
  local drive_folder="${VIEW_DRIVE_FOLDER[$view]}"
  local drive_name="${VIEW_DRIVE_NAME[$view]}"

  echo "=== Capturing $view ($MODE) ==="

  if [[ "$MODE" == "screenshot" ]]; then
    echo "  Running test: $test_case ..."
    local node_output
    if [[ "$DO_PRODUCTION" -eq 1 ]]; then
      node_output=$(run_on_node "UI_TEST_BACKEND=production EWAG_GIT_BRANCH='${EWAG_GIT_BRANCH}' EWAG_GIT_COMMIT='${EWAG_GIT_COMMIT}' $IOS_AGENT_BIN" test --live-demo-auth ${EWAG_GIT_BRANCH:+--branch "$EWAG_GIT_BRANCH"} ${EWAG_GIT_COMMIT:+--commit "$EWAG_GIT_COMMIT"} \
        --case "$test_case")
    else
      node_output=$(run_on_node "EWAG_GIT_BRANCH='${EWAG_GIT_BRANCH}' EWAG_GIT_COMMIT='${EWAG_GIT_COMMIT}' $IOS_AGENT_BIN" test --live-demo-auth ${EWAG_GIT_BRANCH:+--branch "$EWAG_GIT_BRANCH"} ${EWAG_GIT_COMMIT:+--commit "$EWAG_GIT_COMMIT"} \
        --case "$test_case")
    fi

    local runtime_backend
    runtime_backend="$(extract_marker_value "CAPTURE_BACKEND" "$node_output")"
    local runtime_email
    runtime_email="$(extract_marker_value "CAPTURE_LOGIN_EMAIL" "$node_output")"
    local runtime_name_check
    runtime_name_check="$(extract_marker_value "CAPTURE_EXPECTED_NAME_OK" "$node_output")"
    local app_backend
    app_backend="$(extract_marker_value "CAPTURE_APP_BACKEND" "$node_output")"
    local app_base_url
    app_base_url="$(extract_marker_value "CAPTURE_APP_BASE_URL" "$node_output")"

    if [[ -n "$runtime_backend" ]]; then
      echo "  Runtime backend marker: $runtime_backend"
    else
      echo "  Runtime backend marker: (missing)"
    fi
    if [[ -n "$runtime_email" ]]; then
      echo "  Runtime login marker: $runtime_email"
    fi
    if [[ -n "$app_backend" ]]; then
      echo "  App backend marker: $app_backend"
    fi
    if [[ -n "$app_base_url" ]]; then
      echo "  App base URL marker: $app_base_url"
    fi

    if [[ "$DO_PRODUCTION" -eq 1 && -z "$runtime_backend" && -z "$app_base_url" ]]; then
      echo "  Note: runtime markers were not emitted by XCTest stdout; relying on production sanity preflight."
    fi

    local test_status
    test_status="$(extract_marker_value "TEST_STATUS" "$node_output")"
    local test_log
    test_log="$(extract_marker_value "LOG" "$node_output")"
    if [[ "$test_status" != "passed" ]]; then
      echo "  ERROR: test failed (TEST_STATUS=${test_status:-unknown})."
      if [[ -n "$test_log" ]]; then
        echo "  Log: $test_log"
      fi
      echo "  Output (last 500 chars): ${node_output: -500}"
      return 1
    fi

    if [[ "$DO_PRODUCTION" -eq 1 && "$view" == "home" && "$runtime_name_check" == "0" ]]; then
      echo "  ERROR: production identity check failed (expected Railway demo name not confirmed)."
      echo "  Output (last 500 chars): ${node_output: -500}"
      return 1
    fi

    local result_bundle
    result_bundle="$(extract_marker_value "RESULT_BUNDLE" "$node_output")"
    if [[ -z "$result_bundle" ]]; then
      echo "  ERROR: no RESULT_BUNDLE in output."
      echo "  Output (last 500 chars): ${node_output: -500}"
      return 1
    fi

    local bundle_path="$result_bundle"
    if [[ "$bundle_path" != /* ]]; then
      bundle_path="$IOS_AGENT_ARTIFACTS_DIR/$bundle_path"
    fi
    echo "  Bundle: $bundle_path"

    echo "  Extracting screenshot..."
    local export_test_case="$test_case"
    if [[ "$export_test_case" == *_Production ]]; then
      export_test_case="${export_test_case%_Production}"
    fi
    local test_id="${export_test_case}()"
    ssh $SSH_OPTS "$SSH_HOST" "rm -rf /tmp/ss-export && \
      xcrun xcresulttool export attachments \
      --path '$bundle_path' \
      --test-id '$test_id' \
      --output-path /tmp/ss-export" 2>&1

    local remote_png
    remote_png="$(ssh $SSH_OPTS "$SSH_HOST" "ls -1t /tmp/ss-export/*.png 2>/dev/null | head -1")"
    if [[ -z "$remote_png" ]]; then
      # Check if the test produced a skip diagnostic (amenity-gated views)
      local remote_txt
      remote_txt="$(ssh $SSH_OPTS "$SSH_HOST" "ls -1t /tmp/ss-export/*.txt 2>/dev/null | head -1")"
      if [[ -n "$remote_txt" ]]; then
        local skip_content
        skip_content="$(ssh $SSH_OPTS "$SSH_HOST" "cat '$remote_txt'" 2>/dev/null || true)"
        echo "  SKIPPED: $view (amenity not enabled)"
        echo "  Diagnostic: ${skip_content:0:200}"
        RESULTS+=("⏭️  ${drive_name}: skipped (amenity not enabled)")
        return 0
      fi
      echo "  No PNG found for test-id filter, retrying export without test-id..."
      ssh $SSH_OPTS "$SSH_HOST" "rm -rf /tmp/ss-export && \
        xcrun xcresulttool export attachments \
        --path '$bundle_path' \
        --output-path /tmp/ss-export" 2>&1
      remote_png="$(ssh $SSH_OPTS "$SSH_HOST" "ls -1t /tmp/ss-export/*.png 2>/dev/null | head -1")"
      if [[ -z "$remote_png" ]]; then
        echo "  ERROR: screenshot export produced no PNG files."
        return 1
      fi
    fi

    local local_file="$BACKEND_OUTPUT_DIR/${view}-${TIMESTAMP}.png"
    scp $SSH_OPTS "$SSH_HOST:$remote_png" "$local_file" 2>&1
    if [[ ! -s "$local_file" ]]; then
      echo "  ERROR: screenshot file missing or empty."
      return 1
    fi
    echo "  Saved: $local_file ($(du -h "$local_file" | cut -f1))"

    if [[ "$NO_UPLOAD" -eq 0 ]]; then
      local upload_parent
      upload_parent="$(ensure_drive_subfolder "$drive_folder" "$DRIVE_BACKEND_FOLDER_NAME")" || {
        echo "  ERROR: failed to create/find Drive backend folder '${DRIVE_BACKEND_FOLDER_NAME}' under ${drive_name}."
        return 1
      }
      if [[ -z "$upload_parent" ]]; then
        echo "  ERROR: Drive backend folder ID is empty for ${drive_name}/${DRIVE_BACKEND_FOLDER_NAME}."
        return 1
      fi

      local drive_filename="${view}-${DATE_STAMP}.png"
      echo "  Uploading to Drive -> ${drive_name}/${DRIVE_BACKEND_FOLDER_NAME}/${drive_filename} ..."
      local upload_output
      upload_output=$(gog drive upload "$local_file" \
        --parent "$upload_parent" \
        --name "$drive_filename" \
        --json --no-input 2>&1)

      local file_id
      file_id=$(echo "$upload_output" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('file',{}).get('id','') or d.get('id',''))" 2>/dev/null || true)
      if [[ -n "$file_id" ]]; then
        local link="https://drive.google.com/file/d/${file_id}/view"
        echo "  OK: $link"
        RESULTS+=("📸 ${drive_name}: ${link}")
      else
        echo "  Upload output: $upload_output"
        RESULTS+=("📸 ${drive_name}: uploaded (check Drive)")
      fi
    else
      echo "  Skipped upload (--no-upload)"
      RESULTS+=("📸 ${drive_name}: $local_file (local only)")
    fi

  elif [[ "$MODE" == "record" ]]; then
    echo "  Recording test: $test_case ..."
    local node_output
    node_output=$(run_on_node "$IOS_AGENT_BIN" record-test --live-demo-auth \
      --case "$test_case")

    local record_path
    record_path="$(extract_marker_value "RECORD_PATH" "$node_output")"
    if [[ -z "$record_path" ]]; then
      echo "  ERROR: no RECORD_PATH in output."
      echo "  Output (last 500 chars): ${node_output: -500}"
      return 1
    fi

    local remote_record_path="$record_path"
    if [[ "$remote_record_path" != /* ]]; then
      remote_record_path="$IOS_AGENT_ARTIFACTS_DIR/$remote_record_path"
    fi
    echo "  Recording: $remote_record_path"

    local local_mp4="$BACKEND_OUTPUT_DIR/${view}-recording-${TIMESTAMP}.mp4"
    scp $SSH_OPTS "$SSH_HOST:$remote_record_path" "$local_mp4" 2>&1
    if [[ ! -s "$local_mp4" ]]; then
      echo "  ERROR: recording file missing or empty."
      return 1
    fi
    local local_file="$BACKEND_OUTPUT_DIR/${view}-recording-${TIMESTAMP}.webm"
    echo "  Converting MP4 -> WebM ..."
    if ! convert_mp4_to_webm "$local_mp4" "$local_file"; then
      echo "  ERROR: WebM conversion failed."
      return 1
    fi
    rm -f "$local_mp4"
    echo "  Saved: $local_file ($(du -h "$local_file" | cut -f1))"

    if [[ "$NO_UPLOAD" -eq 0 ]]; then
      local upload_parent
      local backend_recordings_folder
      backend_recordings_folder="$(ensure_drive_subfolder "$RECORDINGS_FOLDER" "$DRIVE_BACKEND_FOLDER_NAME")" || {
        echo "  ERROR: failed to create/find Drive backend folder '${DRIVE_BACKEND_FOLDER_NAME}' under Recordings."
        return 1
      }
      if [[ -z "$backend_recordings_folder" ]]; then
        echo "  ERROR: Drive backend folder ID is empty for Recordings/${DRIVE_BACKEND_FOLDER_NAME}."
        return 1
      fi

      upload_parent="$(ensure_drive_subfolder "$backend_recordings_folder" "$drive_name")" || {
        echo "  ERROR: failed to create/find Drive view folder '${drive_name}' under Recordings/${DRIVE_BACKEND_FOLDER_NAME}."
        return 1
      }
      if [[ -z "$upload_parent" ]]; then
        echo "  ERROR: Drive view folder ID is empty for Recordings/${DRIVE_BACKEND_FOLDER_NAME}/${drive_name}."
        return 1
      fi

      local drive_filename="${view}-recording-${DATE_STAMP}.webm"
      echo "  Uploading to Drive -> Recordings/${DRIVE_BACKEND_FOLDER_NAME}/${drive_name}/${drive_filename} ..."
      local upload_output
      upload_output=$(gog drive upload "$local_file" \
        --parent "$upload_parent" \
        --name "$drive_filename" \
        --json --no-input 2>&1)

      local file_id
      file_id=$(echo "$upload_output" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('file',{}).get('id','') or d.get('id',''))" 2>/dev/null || true)
      if [[ -n "$file_id" ]]; then
        local link="https://drive.google.com/file/d/${file_id}/view"
        echo "  OK: $link"
        RESULTS+=("🎬 ${drive_name}: ${link}")
      else
        echo "  Upload output: $upload_output"
        RESULTS+=("🎬 ${drive_name}: uploaded (check Drive)")
      fi
    else
      echo "  Skipped upload (--no-upload)"
      RESULTS+=("🎬 ${drive_name}: $local_file (local only)")
    fi

  elif [[ "$MODE" == "scroll" ]]; then
    local scroll_test="${VIEW_SCROLL_TEST[$view]}"
    echo "  Scroll-recording test: $scroll_test ..."
    local node_output
    node_output=$(run_on_node "$IOS_AGENT_BIN" record-test --live-demo-auth \
      --case "$scroll_test")

    local record_path
    record_path="$(extract_marker_value "RECORD_PATH" "$node_output")"
    if [[ -z "$record_path" ]]; then
      echo "  ERROR: no RECORD_PATH in output."
      echo "  Output (last 500 chars): ${node_output: -500}"
      return 1
    fi

    local remote_record_path="$record_path"
    if [[ "$remote_record_path" != /* ]]; then
      remote_record_path="$IOS_AGENT_ARTIFACTS_DIR/$remote_record_path"
    fi
    echo "  Recording: $remote_record_path"

    local local_mp4="$BACKEND_OUTPUT_DIR/${view}-scroll-${TIMESTAMP}.mp4"
    scp $SSH_OPTS "$SSH_HOST:$remote_record_path" "$local_mp4" 2>&1
    if [[ ! -s "$local_mp4" ]]; then
      echo "  ERROR: scroll recording file missing or empty."
      return 1
    fi
    local local_file="$BACKEND_OUTPUT_DIR/${view}-scroll-${TIMESTAMP}.webm"
    echo "  Converting MP4 -> WebM (trimming first ${SCROLL_TRIM_SECONDS}s) ..."
    if ! convert_mp4_to_webm "$local_mp4" "$local_file" "$SCROLL_TRIM_SECONDS"; then
      echo "  ERROR: WebM conversion failed."
      return 1
    fi
    rm -f "$local_mp4"
    echo "  Saved: $local_file ($(du -h "$local_file" | cut -f1))"

    if [[ "$NO_UPLOAD" -eq 0 ]]; then
      local upload_parent
      local backend_recordings_folder
      backend_recordings_folder="$(ensure_drive_subfolder "$RECORDINGS_FOLDER" "$DRIVE_BACKEND_FOLDER_NAME")" || {
        echo "  ERROR: failed to create/find Drive backend folder '${DRIVE_BACKEND_FOLDER_NAME}' under Recordings."
        return 1
      }
      if [[ -z "$backend_recordings_folder" ]]; then
        echo "  ERROR: Drive backend folder ID is empty for Recordings/${DRIVE_BACKEND_FOLDER_NAME}."
        return 1
      fi

      upload_parent="$(ensure_drive_subfolder "$backend_recordings_folder" "$drive_name")" || {
        echo "  ERROR: failed to create/find Drive view folder '${drive_name}' under Recordings/${DRIVE_BACKEND_FOLDER_NAME}."
        return 1
      }
      if [[ -z "$upload_parent" ]]; then
        echo "  ERROR: Drive view folder ID is empty for Recordings/${DRIVE_BACKEND_FOLDER_NAME}/${drive_name}."
        return 1
      fi

      local drive_filename="${view}-scroll-${DATE_STAMP}.webm"
      echo "  Uploading to Drive -> Recordings/${DRIVE_BACKEND_FOLDER_NAME}/${drive_name}/${drive_filename} ..."
      local upload_output
      upload_output=$(gog drive upload "$local_file" \
        --parent "$upload_parent" \
        --name "$drive_filename" \
        --json --no-input 2>&1)

      local file_id
      file_id=$(echo "$upload_output" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('file',{}).get('id','') or d.get('id',''))" 2>/dev/null || true)
      if [[ -n "$file_id" ]]; then
        local link="https://drive.google.com/file/d/${file_id}/view"
        echo "  OK: $link"
        RESULTS+=("📜 ${drive_name} scroll: ${link}")
      else
        echo "  Upload output: $upload_output"
        RESULTS+=("📜 ${drive_name} scroll: uploaded (check Drive)")
      fi
    else
      echo "  Skipped upload (--no-upload)"
      RESULTS+=("📜 ${drive_name} scroll: $local_file (local only)")
    fi
  fi
}

BACKEND_LABEL=$([[ "$DO_PRODUCTION" -eq 1 ]] && echo "Railway" || echo "local")
DRIVE_BACKEND_FOLDER_NAME=$([[ "$DO_PRODUCTION" -eq 1 ]] && echo "railway" || echo "local")
BACKEND_OUTPUT_DIR="$MEDIA_DIR/$([[ "$DO_PRODUCTION" -eq 1 ]] && echo "railway" || echo "local")"
EWAG_GIT_BRANCH="${BUILD_BRANCH:-${EWAG_GIT_BRANCH:-}}"
EWAG_GIT_COMMIT="${EWAG_GIT_COMMIT:-}"
mkdir -p "$BACKEND_OUTPUT_DIR"
echo "ewag-capture: mode=$MODE views=${VIEWS[*]} build=$DO_BUILD backend=$BACKEND_LABEL upload=$([[ $NO_UPLOAD -eq 0 ]] && echo yes || echo no)"
echo "local output dir: $BACKEND_OUTPUT_DIR"
echo "drive upload subfolder: $DRIVE_BACKEND_FOLDER_NAME"
echo ""

# --- Preflight: verify node is reachable ---
if ! run_on_node "$IOS_AGENT_BIN" branch >/dev/null 2>&1; then
  echo "ERROR: ios-build-node is not reachable. Check Mac power, Tailscale, and node daemon."
  exit 1
fi
echo "Node preflight: OK"

if [[ "$DO_PRODUCTION" -eq 1 ]]; then
  # --- Production (Railway) mode: health check only, no seeding ---
  RAILWAY_URL="https://backend-production-1013.up.railway.app"
  echo "Checking Railway backend ($RAILWAY_URL) ..."
  BACKEND_HEALTH=$(run_on_node "curl -sS --max-time 15 '$RAILWAY_URL/health'" 2>&1 || true)
  if ! echo "$BACKEND_HEALTH" | grep -q '"status":"ok"'; then
    echo "ERROR: Railway backend not responding at $RAILWAY_URL/health."
    echo "  Health response: $BACKEND_HEALTH"
    exit 1
  fi
  echo "Railway backend health: OK"

  # Persist backend override in simulator defaults so the app can read it
  # even when xcodebuild/test-runner env propagation is inconsistent.
  echo "Configuring simulator backend override: production"
  run_on_node "xcrun simctl boot 'iPhone 16 Pro Max' >/dev/null 2>&1 || true"
  run_on_node "xcrun simctl spawn booted defaults write com.elitepro.resilife UI_TEST_BACKEND production" >/dev/null 2>&1 || {
    echo "ERROR: failed to write simulator backend override for production mode."
    exit 1
  }

  echo "Running production backend sanity test (must resolve to Emma) ..."
  SANITY_OUTPUT=$(run_on_node "UI_TEST_BACKEND=production EWAG_GIT_BRANCH='${EWAG_GIT_BRANCH}' EWAG_GIT_COMMIT='${EWAG_GIT_COMMIT}' $IOS_AGENT_BIN" test --live-demo-auth ${EWAG_GIT_BRANCH:+--branch "$EWAG_GIT_BRANCH"} ${EWAG_GIT_COMMIT:+--commit "$EWAG_GIT_COMMIT"} \
    --case "ResidentUITests/testProductionBackendSanity" 2>&1 || true)
  SANITY_STATUS="$(extract_marker_value "TEST_STATUS" "$SANITY_OUTPUT")"
  if [[ "$SANITY_STATUS" != "passed" ]]; then
    echo "ERROR: production backend sanity failed."
    echo "Output (last 500 chars): ${SANITY_OUTPUT: -500}"
    exit 1
  fi
  echo "Production backend sanity: OK"
else
  # --- Local mode: health check + seed ---
  # The simulator connects to localhost:8080 on the Mac.  Verify the
  # backend is responsive and demo data is seeded before running any tests.
  echo "Checking backend on Mac (localhost:8080) ..."
  BACKEND_HEALTH=$(run_on_node "curl -sS --max-time 10 http://localhost:8080/health" 2>&1 || true)
  if ! echo "$BACKEND_HEALTH" | grep -q '"status":"ok"'; then
    echo "ERROR: Local backend not responding on Mac (localhost:8080)."
    echo "  Start it: cd ~/iosApp/Backend && docker-compose up -d && .build/debug/App serve --hostname 0.0.0.0 --port 8080 &"
    echo "  Health response: $BACKEND_HEALTH"
    exit 1
  fi
  echo "Backend health: OK"

  # Seed (idempotent — uses POST /seed without ?force so it won't wipe existing data).
  echo "Ensuring demo data is seeded ..."
  SEED_TOKEN=$(run_on_node "curl -sS --max-time 10 -X POST http://localhost:8080/api/v1/auth/login \
    -H 'Content-Type: application/json' \
    -d '{\"email\":\"demo@resilife.app\",\"password\":\"Demo1234!\"}'" 2>&1 \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('accessToken') or d.get('access_token') or '')" 2>/dev/null || true)
  if [[ -z "$SEED_TOKEN" || ${#SEED_TOKEN} -lt 20 ]]; then
    echo "ERROR: Could not login as demo@resilife.app. Backend may need migrations or manual seed."
    exit 1
  fi
  SEED_RESULT=$(run_on_node "curl -sS --max-time 120 -X POST 'http://localhost:8080/api/v1/seed' \
    -H 'Authorization: Bearer $SEED_TOKEN'" 2>&1 || true)
  if echo "$SEED_RESULT" | python3 -c "import sys,json; json.load(sys.stdin)" >/dev/null 2>&1; then
    echo "Seed: OK"
  else
    echo "WARNING: Seed response was not JSON (may already be seeded): ${SEED_RESULT:0:200}"
  fi

  # Local mode explicitly clears simulator backend override.
  run_on_node "xcrun simctl boot 'iPhone 16 Pro Max' >/dev/null 2>&1 || true"
  run_on_node "xcrun simctl spawn booted defaults delete com.elitepro.resilife UI_TEST_BACKEND" >/dev/null 2>&1 || true
fi

# --- Pre-capture: clean/build if requested ---
if [[ "$DO_CLEAN" -eq 1 ]]; then
  echo "=== Cleaning build artifacts ==="
  run_on_node "$IOS_AGENT_BIN" clean | tail -5
  echo ""
fi

if [[ "$DO_BUILD" -eq 1 ]]; then
  if [[ -n "$BUILD_BRANCH" ]]; then
    echo "=== Building branch: $BUILD_BRANCH ==="
    BUILD_OUTPUT=$(run_on_node "$IOS_AGENT_BIN" build --branch "$BUILD_BRANCH" ${EWAG_GIT_COMMIT:+--commit "$EWAG_GIT_COMMIT"})
  else
    echo "=== Building current branch ==="
    BUILD_OUTPUT=$(run_on_node "$IOS_AGENT_BIN" build ${EWAG_GIT_BRANCH:+--branch "$EWAG_GIT_BRANCH"} ${EWAG_GIT_COMMIT:+--commit "$EWAG_GIT_COMMIT"})
  fi
  if echo "$BUILD_OUTPUT" | grep -q '"success":false\|BUILD_STATUS=failed'; then
    echo "ERROR: Build failed. Aborting capture."
    echo "$BUILD_OUTPUT" | grep -E 'error:|BUILD|FAILED' | tail -10
    exit 1
  fi
  echo "$BUILD_OUTPUT" | grep -E 'BUILD|SUCCEEDED' | tail -5
  echo ""
fi

# --- Drive access check (skip if --no-upload) ---
if [[ "$NO_UPLOAD" -eq 0 ]]; then
  require_drive_access
fi

# --- Recording preflight (record/scroll require ffmpeg for WebM output) ---
if [[ "$MODE" == "record" || "$MODE" == "scroll" ]]; then
  require_ffmpeg_for_recordings
fi

FAILED=0
for v in "${VIEWS[@]}"; do
  capture_view "$v" || { echo "FAILED: $v"; FAILED=$((FAILED + 1)); }
  echo ""
done

echo "========================================="
if [[ $FAILED -gt 0 ]]; then
  echo "Done with $FAILED failure(s) out of ${#VIEWS[@]} view(s)."
else
  if [[ "$NO_UPLOAD" -eq 1 ]]; then
    echo "All ${#VIEWS[@]} capture(s) completed locally (upload skipped)."
  else
    echo "All ${#VIEWS[@]} capture(s) uploaded to Drive."
  fi
fi
echo ""
for r in "${RESULTS[@]}"; do
  echo "  $r"
done
echo ""
echo "Drive folder: $DRIVE_ROOT_LINK"
