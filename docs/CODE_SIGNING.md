# Code signing policy

## Current status

**Signing integration is prepared, but trusted signing is not active.** Published EXE downloads are currently unsigned. No SignPath Foundation approval, subscription or certificate is claimed. Adding this configuration does not change previously published EXEs, and a signature cannot guarantee that SmartScreen warnings disappear immediately.

The intended free service is [SignPath Foundation](https://signpath.org/), subject to its [project eligibility and signing conditions](https://signpath.org/terms). This is a proposed integration; the foundation decides whether to accept the project. If approved, the required attribution will be: "Free code signing provided by [SignPath.io](https://about.signpath.io/), certificate by [SignPath Foundation](https://signpath.org/)."

## Approval prerequisites

Before submitting the [application](https://signpath.org/apply), the maintainer must resolve the following:

- Confirm the right to redistribute every bundled image, rule definition and dependency. Some resources were recovered from third-party software; provenance records and the project's MIT license do not establish permission to redistribute those resources. Obtain appropriate permission or replace affected material with independently created or suitably licensed resources before representing the package as eligible.
- Enable multi-factor authentication for the repository and signing-service accounts.
- Accept the provider's terms and configure the required project reviews and manual signing approvals. Signing approval must remain an explicit maintainer action for every release.
- Supply the owner's contact details in the application. Credentials and contact details do not belong in this repository or chat.

The project has public source, MIT-licensed original code, documented Windows builds and an existing portable EXE release. These facts are useful application evidence, not a guarantee of acceptance. The maintainer must disclose the third-party resource situation accurately.

## Scope, roles and privacy

- Maintainer, reviewer and proposed signing approver: [jackqiuai](https://github.com/jackqiuai). Changes by outside contributors must be reviewed before release signing.
- Signing is limited to `CPlusCleaner.exe`, built from this repository on GitHub-hosted Windows runners. It does not sign arbitrary uploads or third-party executables with this project's identity.
- This application scans local files and Windows information on request. It does not automatically upload scan results, file contents, settings or history. Opening the GitHub feedback page is an explicit user action in the user's browser; the user decides which information to submit, under [GitHub's privacy statement](https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement).
- The optional donation dialog displays owner-supplied QR images locally or opens the public Ko-fi page only when clicked. Overseas payments take place in the browser under [Ko-fi's privacy policy](https://more.ko-fi.com/privacy) and the selected payment provider's policies; this application does not receive payment credentials or transaction results.
- The build workflow submits the public build artifact and source/build identifiers to SignPath only after signing is configured. It does not submit local application data or signing private keys. The signing provider manages the private key.

## Activate after acceptance

1. Apply at [SignPath Foundation](https://signpath.org/apply) using `https://github.com/jackqiuai/cplus-cleaner` as the project repository. Complete the provider's review and account setup.
2. Install the [SignPath GitHub App](https://github.com/apps/signpath) for this repository and link its GitHub.com trusted build system in SignPath. Use GitHub-hosted runners, a production signing policy, timestamping and the required manual approver. The expected certificate common name is `SignPath Foundation`; if the issued identity differs, review and explicitly update the verification setting in the workflow command.
3. Create an artifact configuration from [`.github/signpath-artifact.xml`](../.github/signpath-artifact.xml). The uploaded Actions artifact is a ZIP containing exactly `CPlusCleaner.exe`. The policy restricts the product name, original filename and version to reviewed source metadata. It signs only that PE file using SHA-256, not upstream libraries inside the PyInstaller bundle. Copy the artifact configuration into SignPath; committing the XML does not configure the external service automatically.
4. In repository **Settings > Secrets and variables > Actions**, add:

   | Kind | Name | Value |
   | --- | --- | --- |
   | Secret | `SIGNPATH_API_TOKEN` | SignPath submitter token, limited to the approved project and policy. |
   | Variable | `SIGNPATH_ORGANIZATION_ID` | Assigned SignPath organization ID. |
   | Variable | `SIGNPATH_PROJECT_SLUG` | Assigned project slug. |
   | Variable | `SIGNPATH_SIGNING_POLICY_SLUG` | Approved production policy slug. |
   | Variable | `SIGNPATH_ARTIFACT_CONFIGURATION_SLUG` | Artifact configuration slug for the checked-in XML. |
   | Variable | `SIGNPATH_ENABLED` | Set to `true` only after all of the above is ready. |

   Enter the token directly in GitHub's secret form; never put it in source, an issue or chat.

5. Update this policy's status and attribution once the service is actually approved. Run the build workflow manually on `main` and approve the request in SignPath within the workflow's one-hour wait. Check the signed artifact and verification diagnostics before publishing a new release.
6. Create a new release from a tag containing this workflow and the matching `preferences.VERSION`. Existing tags containing older workflows will not acquire signing by re-running their old builds.

No certificate, paid subscription or external account is created by this repository configuration.

## Build and release behavior

- Normal `main` pushes and pull requests build unsigned development artifacts, without submitting signing requests. Pull requests never receive signing credentials.
- When signing is enabled, version-tag pushes, published releases and manual runs on `main` or a version tag submit the built EXE to SignPath. All signing inputs are uploaded as an Actions artifact so the provider can verify their origin. Each request requires approval according to the service's configured policy.
- The signing job checks the returned file with Windows Authenticode. It requires a trusted `Valid` embedded signature, a timestamp, the expected certificate publisher and matching product/version/filename metadata. It then runs the isolated EXE smoke check.
- Only after verification does it copy the signed EXE into `dist/`, rebuild the portable ZIP from those signed bytes, and recompute both SHA-256 digests. Neither EXE resources nor version metadata are modified after signing.
- With `SIGNPATH_ENABLED=true`, a failed, rejected or timed-out signature prevents the release-upload job from running. There is no automatic fallback to unsigned release files.
- While signing is disabled or not configured, the existing unsigned build/release behavior remains available. Those files are **not** digitally signed. Do not present them as trusted signed releases.
- Signed artifacts are named `CPlusCleaner-windows-x64-signed`; the unsigned development artifact retains `CPlusCleaner-windows-x64`. Verification diagnostics are uploaded separately.

## Verify a downloaded EXE

On Windows:

```powershell
$signature = Get-AuthenticodeSignature -LiteralPath .\CPlusCleaner.exe
$signature | Select-Object Status, SignatureType, SignerCertificate, TimeStamperCertificate
Get-FileHash -LiteralPath .\CPlusCleaner.exe -Algorithm SHA256
```

For a future signed release, check `Status` is `Valid`, that the signer matches the published signing identity, and that the digest matches that release's `SHA256SUMS.txt`. An unsigned existing release reports `NotSigned`. File properties such as `CompanyName` are descriptive metadata; they do not establish a trusted publisher.

The strict packaging command used by CI is:

```powershell
python tools/build_windows.py --package-only --require-signed --executable signed/CPlusCleaner.exe
```

This command verifies and packages a signed EXE; it does not sign an unsigned file. It never installs a certificate or changes Windows trust/security settings.

See [SignPath's GitHub integration](https://docs.signpath.io/trusted-build-systems/github) and [Microsoft's SmartScreen reputation guidance](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation).

<!-- Purpose: Pending code signing policy, free-service prerequisites and activation instructions -->
