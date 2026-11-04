# Build and release

## Clean Windows clone

Install Git, Rust via rustup, and Visual Studio Build Tools with Desktop development with C++ and a Windows SDK. Use Windows 10/11 x64 with the MSVC toolchain. The committed `rust-toolchain.toml` selects the compiler and `Cargo.lock` locks dependencies. Internet access is needed for the initial toolchain and dependency downloads.

```powershell
git clone https://github.com/jackqiuai/cplus-cleaner.git
cd cplus-cleaner
cargo fmt --all --check
cargo clippy --workspace --all-targets --locked -- -D warnings
./build.ps1
```

`build.ps1` runs workspace tests, builds the release executable and calls `package.ps1`. Output: `dist/CPlusCleaner.exe`, `dist/CPlusCleaner-windows-x64.zip`, `dist/SHA256SUMS.txt`, and `dist/build-validation.json`. The ZIP contains the executable, MIT license, three READMEs and documentation including screenshots. Images/rules/translations used by the application are embedded in the EXE; loose files are not needed at runtime.

## GitHub Actions

The Windows workflow runs on pushes to `main`, version tags, pull requests, published releases and manual dispatch. It installs the committed Rust toolchain, checks formatting and Clippy, runs tests, builds and smoke-checks the executable, then uploads downloadable artifacts. Normal builds need no personal access token or signing key.

Publishing a GitHub Release triggers attachment of the built EXE, ZIP and checksums to that release. Pushing a version tag alone produces an Actions artifact; create/publish the corresponding Release to attach release assets. Publishing is limited to the release job's `contents: write` permission. Repository owners must allow Actions to run.

Optional SignPath signing remains disabled unless `SIGNPATH_ENABLED=true`; see [CODE_SIGNING.md](CODE_SIGNING.md). Without signing, artifacts are unsigned and Windows may display SmartScreen warnings. Never commit certificates, private keys, API tokens or `.env` files.

## Source boundaries

Keep all Rust source, tests, build scripts, Cargo manifests/lockfile, configuration and the complete `resources/` directory. `target/`, `dist/`, local logs, validation output and runtime profiles are ignored. Existing releases and Git history are separate from the current source tree.

The MIT license covers project code; third-party artwork and rule provenance retain their own terms as documented under `resources/`. Retaining a resource is not a new assertion about its licensing.

References: [GitHub Rust CI](https://docs.github.com/en/actions/tutorials/build-and-test-code/rust), [workflow artifacts](https://docs.github.com/en/actions/tutorials/store-and-share-data).