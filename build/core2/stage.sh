#!/usr/bin/env bash
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK="${1:?usage: stage.sh WORKDIR STAGE BRAVE_CORE_REF [MAX_STAGES]}"
STAGE="${2:?stage number is required}"
BRAVE_CORE_REF="${3:?Brave Core ref is required}"
MAX_STAGES="${4:-8}"

[[ "$STAGE" =~ ^[1-8]$ ]] || { echo "stage must be between 1 and 8" >&2; exit 2; }
[[ "$MAX_STAGES" =~ ^[1-8]$ ]] || { echo "max stages must be between 1 and 8" >&2; exit 2; }
(( STAGE <= MAX_STAGES )) || { echo "stage exceeds max stages" >&2; exit 2; }
git check-ref-format --branch "$BRAVE_CORE_REF" >/dev/null ||
  { echo "invalid Brave Core ref: $BRAVE_CORE_REF" >&2; exit 2; }

mkdir -p "$WORK"
WORK="$(cd "$WORK" && pwd -P)"
SRC="$WORK/src"
CORE="$SRC/brave"
OUT="$SRC/out/Release"
SYNC_READY="$WORK/.core2-sync-ready"
SYNC_STARTED="$WORK/.core2-sync-started"
CORE_COMMIT="$WORK/.core2-brave-core-commit"
DEADLINE_EPOCH=$(( $(date +%s) + 240 * 60 ))

emit() {
  if [ -n "${GITHUB_OUTPUT:-}" ]; then
    printf '%s=%s\n' "$1" "$2" >> "$GITHUB_OUTPUT"
  fi
}

log() { printf '==> %s\n' "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

host_arch="$(uname -m)"
[ "$host_arch" = "x86_64" ] ||
  die "Core 2 build requires an x86_64 runner (found $host_arch)"

snapshot_tree() {
  local snapshot="$WORK/.snapshot-stage-$STAGE"
  log "snapshotting source and Ninja state"
  CHROMIX_SNAPSHOT_MAX_SLOTS=16 \
  CHROMIX_SNAPSHOT_MAX_VOLUMES=64 \
  CHROMIX_SNAPSHOT_VOLUME_BYTES=$((2 * 1024 * 1024 * 1024)) \
    bash "$REPO/build/posix/ci-parts.sh" "$WORK" "$snapshot"
  local slot
  for slot in $(seq 1 16); do
    mkdir -p "$snapshot/p$slot"
    printf '%s\n' "$slot" > "$snapshot/p$slot/.snapshot-slot-$slot"
  done
  emit snapshot_ready true
}

handoff() {
  local reason="$1"
  log "$reason; saving resumable checkpoint"
  snapshot_tree
  emit finished false
  if (( STAGE >= MAX_STAGES )); then
    die "stage $STAGE reached the $MAX_STAGES-stage limit before the build completed"
  fi
  exit 0
}

run_bounded() {
  local remaining
  remaining=$(( DEADLINE_EPOCH - $(date +%s) ))
  if (( remaining <= 0 )); then
    return 124
  fi
  timeout -k 90s --signal=TERM "${remaining}s" "$@"
}

run_or_handoff() {
  local label="$1"
  shift
  local status=0
  log "$label"
  run_bounded "$@" || status=$?
  case "$status" in
    0) ;;
    124|137) handoff "$label exceeded this stage's execution budget" ;;
    *) die "$label failed with exit code $status" ;;
  esac
}

mkdir -p "$SRC"
if [ -f "$CORE_COMMIT" ]; then
  recorded_commit="$(cat "$CORE_COMMIT")"
  actual_commit="$(git -C "$CORE" rev-parse HEAD 2>/dev/null)" ||
    die "Brave Core checkout is missing or incomplete"
  [ "$actual_commit" = "$recorded_commit" ] ||
    die "Brave Core commit changed across build stages"
else
  if [ ! -d "$CORE/.git" ]; then
    mkdir -p "$CORE"
    git -C "$CORE" init
    git -C "$CORE" remote add origin https://github.com/brave/brave-core.git
  fi
  [ "$(git -C "$CORE" remote get-url origin)" = "https://github.com/brave/brave-core.git" ] ||
    die "unexpected Brave Core origin URL"
  run_or_handoff "Fetching Brave Core $BRAVE_CORE_REF" \
    git -C "$CORE" fetch --depth=1 --no-tags origin "$BRAVE_CORE_REF"
  git -C "$CORE" checkout --detach FETCH_HEAD
  git -C "$CORE" rev-parse HEAD > "$CORE_COMMIT"
fi

export NPM_CONFIG_STORE_DIR="$WORK/.pnpm-store"
export COREPACK_HOME="$WORK/.corepack"
mkdir -p "$NPM_CONFIG_STORE_DIR" "$COREPACK_HOME"

if [ ! -f "$SYNC_READY" ]; then
  if [ -f "$SYNC_STARTED" ]; then
    run_or_handoff "Resuming Brave/Chromium dependency sync" \
      pnpm run sync --no-history
  else
    : > "$SYNC_STARTED"
    run_or_handoff "Initializing Brave/Chromium dependencies" \
      pnpm run init --no-history
  fi
  : > "$SYNC_READY"
fi

CHROMIUM_FLAGS="$SRC/build/config/compiler_cpu_abi.gn"
[ -f "$CHROMIUM_FLAGS" ] || die "Chromium compiler configuration is missing: $CHROMIUM_FLAGS"
python3 "$REPO/tools/apply_core2_cpu_flags.py" "$CHROMIUM_FLAGS"

cat > "$CORE/.env" <<'ENV'
is_brave_release_build=0
target_arch=x64
gn_args_target_cpu=x64
gn_args_v8_target_cpu=x64
gn_args_is_official_build=true
gn_args_symbol_level=0
gn_args_blink_symbol_level=0
gn_args_v8_symbol_level=0
gn_args_use_debug_fission=false
gn_args_dcheck_always_on=false
ENV

free_gib="$(df -BG --output=avail "$WORK" | tail -n 1 | tr -dc '0-9')"
log "available disk before Chromium compilation: ${free_gib:-unknown} GiB"

run_or_handoff "Building Brave Release for Intel Core 2 (Ninja jobs: 2)" \
  pnpm run build Release --ninja jobs:2

[ -d "$OUT" ] || die "expected Release output directory is missing: $OUT"
[ -f "$OUT/args.gn" ] || die "generated GN arguments are missing: $OUT/args.gn"
grep -qx 'target_cpu = "x64"' "$OUT/args.gn" ||
  die "generated GN arguments do not target x64"
grep -qx 'v8_target_cpu = "x64"' "$OUT/args.gn" ||
  die "generated GN arguments do not target x64 V8"
if [ ! -x "$OUT/brave" ] && [ ! -x "$OUT/chrome" ]; then
  die "build returned success but no Brave/Chromium executable exists in $OUT"
fi
cat > "$WORK/core2-build-result.txt" <<EOF
brave_core_commit=$(cat "$CORE_COMMIT")
cpu_target=core2
target_arch=x64
v8_target_cpu=x64
official_build=true
is_brave_release_build=0
dcheck_always_on=false
symbol_level=0
blink_symbol_level=0
v8_symbol_level=0
use_debug_fission=false
ninja=j:2
EOF
log "Core 2-compatible Brave Release build completed at $OUT"
snapshot_tree
emit finished true
