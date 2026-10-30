#!/usr/bin/env python3
"""Publish and restore validated Core 2 build checkpoints as release assets."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from snapshot_volumes import volume_paths


VOLUME_RE = re.compile(r"^tree\.tar\.zst\.([0-9]{3})$")
STAGE_RE = re.compile(r"^stage-([0-9]{2})-(manifest\.json|tree\.tar\.zst\.[0-9]{3})$")
MAX_VOLUMES = 64


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


def build_manifest(
    repo: str,
    run_id: str,
    attempt: str,
    stage: str,
    brave_core_ref: str,
    core_commit: str,
    volumes: list[Path],
) -> dict:
    run_number, attempt_number, stage_number = validate_identity(run_id, attempt, stage)
    if not 1 <= len(brave_core_ref) <= 200 or any(
        ord(character) < 32 or ord(character) == 127 for character in brave_core_ref
    ):
        fail("Brave Core ref contains unsupported characters")
    if core_commit and not re.fullmatch(r"[0-9a-f]{40}", core_commit):
        fail("Brave Core commit must be a full lowercase Git SHA")
    if not repo or "/" not in repo or len(repo) > 200:
        fail("invalid GitHub repository name")
    if not 1 <= len(volumes) <= MAX_VOLUMES:
        fail(f"checkpoint must contain 1-{MAX_VOLUMES} volumes")

    volume_metadata = []
    for index, volume in enumerate(volumes, 1):
        match = VOLUME_RE.fullmatch(volume.name)
        if match is None or int(match.group(1)) != index:
            fail(f"unexpected checkpoint volume sequence: {volume.name}")
        size = volume.stat().st_size
        if not 0 < size < 2_000_000_000:
            fail(f"release asset must be nonempty and smaller than 2 GB: {volume.name}")
        volume_metadata.append(
            {"asset": f"{stage_prefix(stage_number)}-{volume.name}", "size": size}
        )

    return {
        "format": 1,
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
) -> list[tuple[str, int]]:
    run_number, attempt_number, stage_number = validate_identity(run_id, attempt, stage)
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
        if manifest.get(key) != value:
            fail(f"checkpoint manifest {key} mismatch")
    commit = manifest.get("brave_core_commit")
    if commit and not re.fullmatch(r"[0-9a-f]{40}", str(commit)):
        fail("checkpoint manifest has an invalid Brave Core commit")

    volumes = manifest.get("volumes")
    if not isinstance(volumes, list) or not 1 <= len(volumes) <= MAX_VOLUMES:
        fail("checkpoint manifest has an invalid volume list")
    expected_prefix = stage_prefix(stage_number)
    result = []
    for index, item in enumerate(volumes, 1):
        if not isinstance(item, dict):
            fail("checkpoint manifest contains an invalid volume entry")
        name = item.get("asset")
        size = item.get("size")
        expected_name = f"{expected_prefix}-tree.tar.zst.{index:03d}"
        if name != expected_name:
            fail("checkpoint manifest volume names are not contiguous")
        if not isinstance(size, int) or isinstance(size, bool) or not 0 < size < 2_000_000_000:
            fail(f"checkpoint manifest has an invalid size for {expected_name}")
        result.append((name, size))
    return result


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


def upload_checkpoint(args: argparse.Namespace) -> None:
    if not os.environ.get("GH_TOKEN") and not os.environ.get("GITHUB_TOKEN"):
        fail("GH_TOKEN or GITHUB_TOKEN is required to access release assets")
    run_id, attempt, stage = validate_identity(args.run_id, args.attempt, args.stage)
    volumes = [Path(path) for path in volume_paths(str(args.snapshot))]
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
    )
    tag = release_tag(str(run_id), str(attempt))
    ensure_release(args.repo, tag, str(run_id))

    prefix = stage_prefix(stage)
    for volume, metadata in zip(volumes, manifest["volumes"], strict=True):
        run_gh(
            args.repo,
            ["release", "upload", tag, f"{volume}#{metadata['asset']}", "--clobber"],
        )

    manifest_name = f"{prefix}-manifest.json"
    with tempfile.TemporaryDirectory(prefix="core2-manifest-") as directory:
        manifest_path = Path(directory) / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        run_gh(
            args.repo,
            ["release", "upload", tag, f"{manifest_path}#{manifest_name}", "--clobber"],
        )

    # Keep the new checkpoint and its manifest before pruning older stages.
    asset_names = run_gh(
        args.repo,
        ["release", "view", tag, "--json", "assets", "--jq", ".assets[].name"],
        capture=True,
    ).splitlines()
    for name in asset_names:
        match = STAGE_RE.fullmatch(name)
        if match and int(match.group(1)) < stage:
            run_gh(args.repo, ["release", "delete-asset", tag, name, "--yes"])
    print(
        f"Published {len(volumes)} checkpoint volume(s) for run {run_id}, "
        f"attempt {attempt}, stage {stage} to release {tag}"
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
    run_gh(
        args.repo,
        ["release", "download", tag, "--pattern", f"{prefix}-manifest.json", "--dir", str(destination)],
    )
    manifest_path = destination / f"{prefix}-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read checkpoint manifest: {error}")
    volumes = validate_manifest(
        manifest, args.repo, str(run_id), str(attempt), str(stage), args.brave_core_ref
    )
    run_gh(
        args.repo,
        ["release", "download", tag, "--pattern", f"{prefix}-tree.tar.zst.*", "--dir", str(destination)],
    )

    expected_names = {name for name, _ in volumes}
    downloaded_names = {
        path.name
        for path in destination.iterdir()
        if path.name.startswith(f"{prefix}-tree.tar.zst.")
    }
    if downloaded_names != expected_names:
        fail("downloaded checkpoint volume set does not match its manifest")
    for name, size in volumes:
        path = destination / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size != size:
            fail(f"checkpoint volume size/type mismatch: {name}")
        normalized = destination / name.removeprefix(f"{prefix}-")
        path.replace(normalized)
    volume_paths(str(destination))
    print(
        f"Downloaded and verified {len(volumes)} checkpoint volume(s) for "
        f"run {run_id}, attempt {attempt}, stage {stage}"
    )


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    upload = commands.add_parser("upload")
    upload.add_argument("--repo", required=True)
    upload.add_argument("--run-id", required=True)
    upload.add_argument("--attempt", required=True)
    upload.add_argument("--stage", required=True)
    upload.add_argument("--brave-core-ref", required=True)
    upload.add_argument("--snapshot", type=Path, required=True)
    upload.add_argument("--workdir", type=Path, required=True)
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
        if args.command == "upload":
            upload_checkpoint(args)
        else:
            download_checkpoint(args)
    except (CheckpointError, OSError, subprocess.SubprocessError) as error:
        print(f"Core 2 release checkpoint: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
