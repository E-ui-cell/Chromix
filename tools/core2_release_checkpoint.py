#!/usr/bin/env python3
"""Stream, publish, and restore validated Core 2 checkpoints as release assets."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from snapshot_volumes import volume_paths


CHUNK_BYTES = 1_800_000_000
READ_BYTES = 8 * 1024 * 1024
MAX_VOLUMES = 64
STAGE_RE = re.compile(r"^stage-([0-9]{2})-(manifest\.json|tree\.tar\.zst\.[0-9]{3})$")


class CheckpointError(ValueError):
    """A Core 2 release checkpoint is missing, mismatched, or invalid."""


def fail(message: str) -> None:
    raise CheckpointError(message)


def validate_identity(run_id: str, attempt: str, stage: str) -> tuple[int, int, int]:
    if not re.fullmatch(r"[0-9]{1,20}", run_id):
        fail("workflow run ID must be numeric")
    if not re.fullmatch(r"[1-9][0-9]{0,5}", attempt):
        fail("workflow attempt must be a positive integer")
    if not re.fullmatch(r"[1-8]", stage):
        fail("stage must be between 1 and 8")
    return int(run_id), int(attempt), int(stage)


def release_tag(run_id: str, attempt: str) -> str:
    return f"core2-brave-{int(run_id)}-attempt-{int(attempt)}"


def stage_prefix(stage: int) -> str:
    return f"stage-{stage:02d}"


def validate_ref(repo: str, brave_core_ref: str) -> None:
    if not repo or repo.count("/") != 1 or len(repo) > 200:
        fail("invalid GitHub repository name")
    if not 1 <= len(brave_core_ref) <= 200 or any(
        ord(character) < 32 or ord(character) == 127 for character in brave_core_ref
    ):
        fail("Brave Core ref contains unsupported characters")


def build_manifest(
    repo: str,
    run_id: str,
    attempt: str,
    stage: str,
    brave_core_ref: str,
    core_commit: str,
    volumes: list[dict],
    *,
    complete: bool,
) -> dict:
    run_number, attempt_number, stage_number = validate_identity(run_id, attempt, stage)
    validate_ref(repo, brave_core_ref)
    if core_commit and not re.fullmatch(r"[0-9a-f]{40}", core_commit):
        fail("Brave Core commit must be a full lowercase Git SHA")
    if not 1 <= len(volumes) <= MAX_VOLUMES:
        fail(f"checkpoint must contain 1-{MAX_VOLUMES} volumes")

    volume_metadata = []
    prefix = stage_prefix(stage_number)
    for index, volume in enumerate(volumes, 1):
        expected_asset = f"{prefix}-tree.tar.zst.{index:03d}"
        if volume.get("asset") != expected_asset:
            fail("checkpoint volume names are not contiguous")
        size = volume.get("size")
        digest = volume.get("sha256")
        if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= CHUNK_BYTES:
            fail(f"checkpoint manifest has an invalid size for {expected_asset}")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            fail(f"checkpoint manifest has an invalid digest for {expected_asset}")
        volume_metadata.append({"asset": expected_asset, "size": size, "sha256": digest})

    return {
        "format": 1,
        "complete": complete,
        "repository": repo,
        "run_id": run_number,
        "attempt": attempt_number,
        "stage": stage_number,
        "brave_core_ref": brave_core_ref,
        "brave_core_commit": core_commit,
        "volumes": volume_metadata,
    }


def validate_manifest(
    manifest: object,
    repo: str,
    run_id: str,
    attempt: str,
    stage: str,
    brave_core_ref: str,
    *,
    require_complete: bool,
) -> list[dict]:
    run_number, attempt_number, stage_number = validate_identity(run_id, attempt, stage)
    validate_ref(repo, brave_core_ref)
    if not isinstance(manifest, dict):
        fail("checkpoint manifest must be a JSON object")
    expected = {
        "format": 1,
        "repository": repo,
        "run_id": run_number,
        "attempt": attempt_number,
        "stage": stage_number,
        "brave_core_ref": brave_core_ref,
    }
    for key, value in expected.items():
        actual = manifest.get(key)
        if key in {"format", "run_id", "attempt", "stage"} and (
            not isinstance(actual, int) or isinstance(actual, bool)
        ):
            fail(f"checkpoint manifest {key} has an invalid type")
        if actual != value:
            fail(f"checkpoint manifest {key} mismatch")
    if not isinstance(manifest.get("complete"), bool):
        fail("checkpoint manifest has an invalid completion marker")
    if require_complete and manifest["complete"] is not True:
        fail("checkpoint manifest is not marked complete")
    commit = manifest.get("brave_core_commit")
    if commit and not re.fullmatch(r"[0-9a-f]{40}", str(commit)):
        fail("checkpoint manifest has an invalid Brave Core commit")

    volumes = manifest.get("volumes")
    if not isinstance(volumes, list):
        fail("checkpoint manifest has an invalid volume list")
    return build_manifest(
        repo,
        str(run_number),
        str(attempt_number),
        str(stage_number),
        brave_core_ref,
        str(commit or ""),
        volumes,
        complete=bool(manifest.get("complete")),
    )["volumes"]


def run_gh(repo: str, args: list[str], *, capture: bool = False) -> str:
    if not os.environ.get("GH_TOKEN") and not os.environ.get("GITHUB_TOKEN"):
        fail("GH_TOKEN or GITHUB_TOKEN is required to access release assets")
    result = subprocess.run(
        ["gh", *args, "--repo", repo],
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        check=False,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout or "").strip() if capture else ""
        fail(f"gh {' '.join(args[:3])} failed with exit code {result.returncode}: {detail}")
    return result.stdout.strip() if capture else ""


def ensure_release(repo: str, tag: str, run_id: str) -> None:
    result = subprocess.run(
        ["gh", "release", "view", tag, "--repo", repo],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode == 0:
        return
    run_gh(
        repo,
        [
            "release",
            "create",
            tag,
            "--title",
            f"Core 2 Brave build checkpoint {run_id}",
            "--notes",
            "Temporary resumable Core 2 Brave build checkpoint. "
            "Only the latest completed stage is retained.",
            "--prerelease",
        ],
    )


def upload_manifest(repo: str, tag: str, manifest: dict) -> None:
    manifest_name = f"{stage_prefix(manifest['stage'])}-manifest.json"
    with tempfile.TemporaryDirectory(prefix="core2-manifest-") as directory:
        manifest_path = Path(directory) / manifest_name
        manifest_path.write_text(
            json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        run_gh(
            repo,
            ["release", "upload", tag, str(manifest_path), "--clobber"],
        )


def download_manifest(repo: str, tag: str, manifest_name: str, directory: Path) -> dict:
    run_gh(
        repo,
        ["release", "download", tag, "--pattern", manifest_name, "--dir", str(directory)],
    )
    path = directory / manifest_name
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read checkpoint manifest: {error}")
    if not isinstance(payload, dict):
        fail("checkpoint manifest must be a JSON object")
    return payload


def release_assets(repo: str, tag: str) -> dict[str, int]:
    raw = run_gh(
        repo,
        ["release", "view", tag, "--json", "assets", "--jq", ".assets"],
        capture=True,
    )
    try:
        assets = json.loads(raw)
    except json.JSONDecodeError as error:
        fail(f"cannot parse release asset listing: {error}")
    if not isinstance(assets, list):
        fail("release asset listing is invalid")
    result = {}
    for asset in assets:
        if not isinstance(asset, dict) or not isinstance(asset.get("name"), str):
            fail("release contains an invalid asset record")
        size = asset.get("size")
        if not isinstance(size, int) or isinstance(size, bool):
            fail(f"release asset has invalid size: {asset['name']}")
        result[asset["name"]] = size
    return result


def prune_previous_stages(repo: str, tag: str, stage: int) -> None:
    raw = run_gh(
        repo,
        ["release", "view", tag, "--json", "assets", "--jq", ".assets[].name"],
        capture=True,
    )
    for name in raw.splitlines():
        match = STAGE_RE.fullmatch(name)
        if match and int(match.group(1)) < stage:
            run_gh(repo, ["release", "delete-asset", tag, name, "--yes"])


def stream_upload(args: argparse.Namespace) -> None:
    if not os.environ.get("GH_TOKEN") and not os.environ.get("GITHUB_TOKEN"):
        fail("GH_TOKEN or GITHUB_TOKEN is required to access release assets")
    run_id, attempt, stage = validate_identity(args.run_id, args.attempt, args.stage)
    validate_ref(args.repo, args.brave_core_ref)
    tag = release_tag(str(run_id), str(attempt))
    ensure_release(args.repo, tag, str(run_id))

    temp_dir = args.temp_dir
    if temp_dir.is_symlink() or not temp_dir.is_dir():
        fail("checkpoint temporary directory must exist and must not be a symlink")
    prefix = stage_prefix(stage)
    volumes = []
    total_bytes = 0
    stream = sys.stdin.buffer

    while True:
        index = len(volumes) + 1
        if index > MAX_VOLUMES:
            if stream.read(1):
                fail(f"checkpoint exceeds the {MAX_VOLUMES}-volume release limit")
            break
        asset_name = f"{prefix}-tree.tar.zst.{index:03d}"
        temp_path = temp_dir / asset_name
        size = 0
        digest = hashlib.sha256()
        try:
            with temp_path.open("xb") as chunk:
                while size < CHUNK_BYTES:
                    block = stream.read(min(READ_BYTES, CHUNK_BYTES - size))
                    if not block:
                        break
                    chunk.write(block)
                    digest.update(block)
                    size += len(block)
            if size == 0:
                temp_path.unlink()
                break
            run_gh(
                args.repo,
                ["release", "upload", tag, str(temp_path), "--clobber"],
            )
            volumes.append({"asset": asset_name, "size": size, "sha256": digest.hexdigest()})
            total_bytes += size
        finally:
            temp_path.unlink(missing_ok=True)
        print(f"Uploaded checkpoint chunk {index:03d} ({size} bytes)", file=sys.stderr, flush=True)

    commit_file = args.workdir / ".core2-brave-core-commit"
    commit = commit_file.read_text(encoding="utf-8").strip() if commit_file.is_file() else ""
    manifest = build_manifest(
        args.repo,
        str(run_id),
        str(attempt),
        str(stage),
        args.brave_core_ref,
        commit,
        volumes,
        complete=False,
    )
    upload_manifest(args.repo, tag, manifest)
    print(
        f"Streamed {total_bytes} bytes in {len(volumes)} provisional checkpoint chunk(s); "
        "completion will be committed after the archive pipeline succeeds",
        file=sys.stderr,
        flush=True,
    )


def finalize_checkpoint(args: argparse.Namespace) -> None:
    run_id, attempt, stage = validate_identity(args.run_id, args.attempt, args.stage)
    tag = release_tag(str(run_id), str(attempt))
    prefix = stage_prefix(stage)
    with tempfile.TemporaryDirectory(prefix="core2-finalize-") as directory:
        manifest = download_manifest(args.repo, tag, f"{prefix}-manifest.json", Path(directory))
        volumes = validate_manifest(
            manifest,
            args.repo,
            str(run_id),
            str(attempt),
            str(stage),
            args.brave_core_ref,
            require_complete=False,
        )

    assets = release_assets(args.repo, tag)
    for volume in volumes:
        if assets.get(volume["asset"]) != volume["size"]:
            fail(f"checkpoint release asset is missing or has the wrong size: {volume['asset']}")

    manifest["complete"] = True
    upload_manifest(args.repo, tag, manifest)
    prune_previous_stages(args.repo, tag, stage)
    print(
        f"Committed checkpoint run {run_id}, attempt {attempt}, stage {stage}; "
        "previous stage assets pruned"
    )


def download_checkpoint(args: argparse.Namespace) -> None:
    if not os.environ.get("GH_TOKEN") and not os.environ.get("GITHUB_TOKEN"):
        fail("GH_TOKEN or GITHUB_TOKEN is required to access release assets")
    run_id, attempt, stage = validate_identity(args.run_id, args.attempt, args.stage)
    destination = args.destination
    if destination.is_symlink():
        fail("checkpoint destination must not be a symlink")
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        fail(f"checkpoint destination is not empty: {destination}")

    tag = release_tag(str(run_id), str(attempt))
    prefix = stage_prefix(stage)
    manifest = download_manifest(args.repo, tag, f"{prefix}-manifest.json", destination)
    volumes = validate_manifest(
        manifest,
        args.repo,
        str(run_id),
        str(attempt),
        str(stage),
        args.brave_core_ref,
        require_complete=True,
    )
    run_gh(
        args.repo,
        ["release", "download", tag, "--pattern", f"{prefix}-tree.tar.zst.*", "--dir", str(destination)],
    )

    expected_names = {volume["asset"] for volume in volumes}
    downloaded_names = {
        path.name
        for path in destination.iterdir()
        if path.name.startswith(f"{prefix}-tree.tar.zst.")
    }
    if downloaded_names != expected_names:
        fail("downloaded checkpoint volume set does not match its manifest")
    for volume in volumes:
        path = destination / volume["asset"]
        if path.is_symlink() or not path.is_file() or path.stat().st_size != volume["size"]:
            fail(f"checkpoint volume size/type mismatch: {volume['asset']}")
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda: source.read(READ_BYTES), b""):
                digest.update(block)
        if digest.hexdigest() != volume["sha256"]:
            fail(f"checkpoint volume checksum mismatch: {volume['asset']}")
        path.replace(destination / volume["asset"].removeprefix(f"{prefix}-"))
    volume_paths(str(destination))
    print(
        f"Downloaded and verified {len(volumes)} checkpoint volume(s) for "
        f"run {run_id}, attempt {attempt}, stage {stage}"
    )


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)

    stream = commands.add_parser("stream-upload")
    stream.add_argument("--repo", required=True)
    stream.add_argument("--run-id", required=True)
    stream.add_argument("--attempt", required=True)
    stream.add_argument("--stage", required=True)
    stream.add_argument("--brave-core-ref", required=True)
    stream.add_argument("--workdir", type=Path, required=True)
    stream.add_argument("--temp-dir", type=Path, required=True)

    finalize = commands.add_parser("finalize")
    finalize.add_argument("--repo", required=True)
    finalize.add_argument("--run-id", required=True)
    finalize.add_argument("--attempt", required=True)
    finalize.add_argument("--stage", required=True)
    finalize.add_argument("--brave-core-ref", required=True)

    download = commands.add_parser("download")
    download.add_argument("--repo", required=True)
    download.add_argument("--run-id", required=True)
    download.add_argument("--attempt", required=True)
    download.add_argument("--stage", required=True)
    download.add_argument("--brave-core-ref", required=True)
    download.add_argument("--destination", type=Path, required=True)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "stream-upload":
            stream_upload(args)
        elif args.command == "finalize":
            finalize_checkpoint(args)
        else:
            download_checkpoint(args)
    except (CheckpointError, OSError, subprocess.SubprocessError) as error:
        print(f"Core 2 release checkpoint: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
