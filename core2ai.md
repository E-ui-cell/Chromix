# Brave/Chromium build flags for Intel Core 2

This is a reference for the settings used by [`.github/workflows/d630-core2-brave-source.yml`](../.github/workflows/d630-core2-brave-source.yml). The workflow is the source of truth for the build; this document is not a separate Codespaces build recipe.

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
gn_args_is_official_build=true
gn_args_symbol_level=0
gn_args_blink_symbol_level=0
gn_args_v8_symbol_level=0
gn_args_dcheck_always_on=false
```

It builds into `src/out/Release`. The workflow's build fingerprint also records these settings:

```text
symbol_level=0
blink_symbol_level=0
v8_symbol_level=0
use_debug_fission=false
ninja=j:2
```

The three symbol-level settings reduce debug-symbol output and disk use. `use_debug_fission=false` disables debug fission. `ninja=j:2` is the workflow's build-wrapper concurrency setting (two jobs), not a CPU instruction or a GN compiler flag.

`gn_args_is_official_build=true` selects Chromium's optimized official-build configuration. It does **not** make this an official Brave release: `is_brave_release_build=0`, and the workflow supplies placeholder values for API/service credentials.

## Important distinctions

- **Do not set `is_component_build=true` just to save disk space.** The current workflow builds the `Release` configuration and does not set that option. A component build changes the output layout; it is not a resumability mechanism. Incremental progress is handled by the workflow's checkpointed output tree.
- **Do not copy Debian package flags into Chromium globally.** The `-O` level and most toolchain optimization choices are controlled by Chromium's GN toolchain. The Debian package-test flags `-ffast-math` and `-funroll-loops` are not part of this browser workflow and should not be added indiscriminately to browser code.
- **LTO is not explicitly disabled by the settings listed above.** Because `is_official_build=true` selects upstream official-build defaults, do not describe this configuration as “no LTO” without checking the generated `args.gn` and effective compiler/linker commands. A no-ThinLTO variant would be a deliberate, separately verified configuration change.
- **CPU flags do not enable GPU features.** The Core 2 flags do not guarantee hardware video decoding, Vulkan, or accelerated rendering on the legacy Intel GM965/X3100 GPU. The workflow's generated GN arguments and runtime GPU diagnostics must be checked separately before claiming any of those features are enabled.

## Build environment constraints

The current GitHub Actions workflow checks for at least 100 GiB of free disk space before source compilation and explicitly disables swap. The draft's 8 GiB swapfile, Codespaces idle-keepalive loop, and `is_component_build=true` instructions do not describe this workflow and have been removed.

Source synchronization, dependency installation, checkpoint restore/resume, compilation, and packaging remain implemented in `.github/workflows/d630-core2-brave-source.yml`; change that workflow—not this reference text—when actual build behavior needs to change.
