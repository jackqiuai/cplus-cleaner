# Optional code signing

The Windows workflow includes an optional SignPath signing job. It is disabled unless the repository variable `SIGNPATH_ENABLED` equals `true`. A locally built EXE is unsigned; configuring a workflow does not issue a certificate or remove SmartScreen reputation warnings.

Before enabling signing, obtain approval from the signing service and confirm redistribution rights for all bundled third-party resources. The original source license does not grant rights to third-party images or rules.

## Repository configuration

| Setting | Purpose |
| --- | --- |
| Secret `SIGNPATH_API_TOKEN` | Signing service API authentication |
| Variable `SIGNPATH_ENABLED` | Explicitly enable the signing job |
| Variable `SIGNPATH_ORGANIZATION_ID` | Approved organization identifier |
| Variable `SIGNPATH_PROJECT_SLUG` | Approved project identifier |
| Variable `SIGNPATH_SIGNING_POLICY_SLUG` | Reviewed signing policy |
| Variable `SIGNPATH_ARTIFACT_CONFIGURATION_SLUG` | Artifact configuration matching this Rust executable |

The artifact configuration must accept the Rust build's `CPlusCleaner.exe`; review the configuration whenever the binary format changes. Keep maintainer approval enabled in the service. Do not place credentials in source files.

Pull requests cannot request signing. The configured eligible events are published releases, version tags and manual runs against main or a version tag. The workflow submits the build artifact with provenance, waits for approval, then checks Authenticode validity before packaging. A release cannot fall back to the unsigned artifact when signing is enabled and fails.

For an already signed executable, run `package.ps1 -Executable <path> -RequireSigned`. The script rejects an invalid or missing trusted signature. Publishing remains a separate action; no release or signing request has been submitted by merely building locally.
