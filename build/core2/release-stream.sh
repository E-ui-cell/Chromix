#!/usr/bin/env bash
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
exec python3 "$REPO/tools/core2_release_checkpoint.py" stream-upload \
  --repo "${GITHUB_REPOSITORY:?}" \
  --run-id "${GITHUB_RUN_ID:?}" \
  --attempt "${GITHUB_RUN_ATTEMPT:?}" \
  --stage "${CORE2_STAGE:?}" \
  --brave-core-ref "${BRAVE_CORE_REF:?}" \
  --workdir "${CORE2_WORKDIR:?}" \
  --temp-dir "${CORE2_CHECKPOINT_TEMP_DIR:?}"
