# Brave/Chromium build flags for Intel Core 2

This documents the settings used by [`.github/workflows/d630-core2-brave-source.yml`](.github/workflows/d630-core2-brave-source.yml), which chains resumable jobs implemented by [`.github/workflows/d630-core2-brave-stage.yml`](.github/workflows/d630-core2-brave-stage.yml). The workflows are the build source of truth.

## Target CPU

The workflow patches Chromium's common x64 compiler ABI flags in `src/build/config/compiler_cpu_abi.gn`:

```gn
cpu_abi_cflags += [
    "-msse3",
    "-march=core2",
    "-mtune=core2",
]
```

- `-march=core2` selects the Intel Core 2 instruction-set target.
- `-mtune=core2` tunes code generation for Core 2 scheduling.
- `-msse3` is retained explicitly in the x64 ABI flags.
- Do not use `-march=native`: GitHub's build runner is not the target laptop, so host detection would target the wrong CPU.

## Brave/Chromium build profile

The workflow creates Brave's `.env` with these relevant settings:

```dotenv
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
```

It builds into `src/out/Release`. On success, the workflow writes
`core2-build-result.txt` with the Brave Core commit and a summary of the key
build settings:

```text
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
```

The three symbol-level settings reduce debug-symbol output and disk use.
`use_debug_fission=false` disables debug fission. `ninja=j:2` is the workflow's
build-wrapper concurrency setting (two jobs), not a CPU instruction or a GN
compiler flag.

The runner must be x86_64, and both Chromium and V8 GN targets are explicitly
set to x64. The final stage checks the generated `args.gn` before accepting the
executable.

`gn_args_is_official_build=true` selects Chromium's optimized official-build configuration. It does **not** make this an official Brave release: `is_brave_release_build=0`. The workflow does not publish a release or include private Brave service credentials; some online Brave services may therefore be unavailable in the resulting build.

## Important distinctions

- **Do not set `is_component_build=true` just to save disk space.** The current workflow builds the `Release` configuration and does not set that option. A component build changes the output layout; it is not a resumability mechanism. Incremental progress is handled by the workflow's checkpointed output tree.
- **Do not copy Debian package flags into Chromium globally.** The `-O` level and most toolchain optimization choices are controlled by Chromium's GN toolchain. The Debian package-test flags `-ffast-math` and `-funroll-loops` are not part of this browser workflow and should not be added indiscriminately to browser code.
- **LTO is not explicitly disabled by the settings listed above.** Because `is_official_build=true` selects upstream official-build defaults, do not describe this configuration as “no LTO” without checking the generated `args.gn` and effective compiler/linker commands. A no-ThinLTO variant would be a deliberate, separately verified configuration change.
- **CPU flags do not enable GPU features.** The Core 2 flags do not guarantee hardware video decoding, Vulkan, or accelerated rendering on the legacy Intel GM965/X3100 GPU. The workflow's generated GN arguments and runtime GPU diagnostics must be checked separately before claiming any of those features are enabled.

## Staged build and runner requirements

Dispatch `d630-core2-brave-source.yml`; it defaults to the standard
`ubuntu-22.04` GitHub-hosted Linux runner, matching this repository's existing
staged Chromium workflow. Each stage records CPU, memory, and disk, then frees
unused preinstalled toolchains before installing build dependencies. It does
not require a paid larger runner or enforce an unverified fixed disk threshold.
The Codespace's `/tmp` capacity is unrelated: `/tmp`, `$RUNNER_TEMP`, and the
workspace on each GitHub-hosted runner are temporary runner storage, recreated
for every job. The workflow logs the filesystem size and free bytes for those
paths before and after cleanup; the stage script also reports free space just
before compilation. The first run must confirm enough room for the restored
source, build output, and checkpoint rather than assuming `/tmp` has 100 GiB.
If the measured space is insufficient, the build needs a smaller footprint or
a runner with more disk.

The workflow allows up to eight sequential jobs, each below the six-hour job
limit. Each stage runs for up to four hours, then stores a zstd-compressed,
metadata-preserving snapshot of the Brave/Chromium source and `out/Release`
tree. The next job restores the snapshot and resumes with Ninja's incremental
state. The checkpoint is split across GitHub Actions artifacts and retained for
seven days; checkpoints are not stored in Releases by this workflow.
If a run stops after publishing a checkpoint, dispatch a new run with
`resume_run_id`, `resume_stage`, and `resume_attempt` set to the source run's
values. The checkpoint must still be within its seven-day artifact retention
window, and use the same Brave Core ref. Disk availability is logged every five
minutes during compilation to help determine whether the checkout and the
checkpoint fit on the standard runner.
The final stage also uploads a checkpoint containing `src/out/Release`; the
browser executable is `brave` (or `chrome` if provided by the selected source).

The sync phase uses `pnpm run init --no-history` once, then `pnpm run sync
--no-history` if initialization was interrupted. Each later stage validates the
same Brave Core commit before using the existing source tree. No idle-keepalive
loop is used: the build runs on GitHub Actions, independently of the laptop or
Codespace.

The staged workflow currently builds the selected Brave Core ref, defaulting
to `v1.101.7`; it does not apply the separate Chromix patch series. Change the
workflow or stage script—not this reference text—when build behavior needs to
change.
