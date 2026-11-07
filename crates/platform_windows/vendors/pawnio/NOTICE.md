# PawnIO components used by CPlus Cleaner

Copyright: namazso and respective upstream contributors.

The unmodified official PawnIO installer 2.2.0 is distributed under its vendor's
proprietary binary terms, which allow redistribution of the original installer.
It is installed only after the user explicitly enables CPU temperature and
accepts Windows administrator authorization. An existing compatible signed
installation is reused; CPlus Cleaner does not uninstall the shared PawnIO driver.

- https://github.com/namazso/PawnIO.Setup/releases/tag/2.2.0
- https://github.com/namazso/PawnIO.Modules/wiki/Using-PawnIO-Modules

IntelMSR.bin and AMDFamily17.bin are unmodified official signed modules 0.2.11,
licensed LGPL-2.1-or-later. Their corresponding sources IntelMSR.p, AMDFamily17.p
and unchanged COPYING are provided alongside them in the source tree. The full
upstream source for this exact version is available at:
https://github.com/namazso/PawnIO.Modules/tree/0.2.11

The module sources include pawnio.inc from that upstream release. Source and
license terms remain those of the upstream authors; the project MIT license
does not relicense any bundled third-party component.

Official binary digests are pinned in src/monitor/cpu_sensor.rs. The application
exposes only fixed CPU temperature reads, not module upload, register selection
or write operations. This implementation is independent of MangoDisk source.
