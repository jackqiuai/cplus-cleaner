# Optional CPU temperature support

CPU temperature uses the official signed PawnIO driver and signed IntelMSR or
AMDFamily17 module. Intel package DTS and identified AMD Zen families 17h, 19h,
and 1Ah are supported; unsupported or multi-package configurations remain
unavailable. Hardware availability must be verified by a real sensor read.

Click **Enable CPU temperature** in system status. Windows requests administrator
authorization once to install the independent `CPlusCleanerCpuTemperature`
LocalSystem service under Program Files. A compatible signed PawnIO installation
is reused; otherwise the bundled verified official installer is invoked.

The service only performs fixed temperature operations. It has no user-supplied
register or module interface. It samples every four seconds and writes a bounded
read-only snapshot under ProgramData/CPlusCleanerCpuTemperature. The folder is
owned by SYSTEM and writable only by SYSTEM and Administrators. GUI reads reject
malformed, future, or expired readings. AMD SMN access uses the shared Access_PCI
mutex to coordinate with other hardware-monitoring applications.

Installation errors are reported in the application's information window.
Existing disk temperature is retained. CPU temperature comes only from the
PawnIO service; other thermal zones are never substituted as CPU temperature.
No GPU changes are included.

To remove only CPlus Cleaner's temperature service, run an administrator terminal:

```powershell
.\CPlusCleaner.exe --uninstall-cpu-temperature
```

This stops and unregisters only CPlus Cleaner's service and removes its known
files. PawnIO is retained because it may be used by other applications.

Third-party notices and the full module license are available offline through
**About → Third-party licenses**. Corresponding module sources and license:
`crates/platform_windows/vendors/pawnio/`.
