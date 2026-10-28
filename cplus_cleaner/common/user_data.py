"""Current user-data location and a guarded, one-time legacy directory rename."""
import os
from pathlib import Path
import stat


DIRECTORY_NAME = 'CPLUSCLEANER'
LEGACY_DIRECTORY_NAME = 'CCLEANNER'


def data_directory(base=None):
    parent = Path(base) if base is not None else Path(os.environ.get('LOCALAPPDATA', str(Path.home())))
    return parent/DIRECTORY_NAME


def _directory_exists(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if (stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400
            or not stat.S_ISDIR(info.st_mode)):
        raise OSError('User data must be an ordinary directory')
    return True


def migrate_legacy_data(base=None):
    """Rename the whole old profile before loading settings; never overwrite a new profile.

    Call only while holding the desktop's single-instance guard. Importing this
    module, read-only helpers and bundle checks do not migrate real user files.
    """
    destination = data_directory(base).absolute()
    parent = destination.parent.resolve()
    destination = parent/DIRECTORY_NAME
    legacy = parent/LEGACY_DIRECTORY_NAME
    # Both fixed names must resolve to siblings inside the explicitly chosen
    # user-data parent. Reject redirects instead of following them during a move.
    target_exists = _directory_exists(destination)
    if target_exists and any(destination.iterdir()):
        return destination
    if not _directory_exists(legacy):
        return destination
    if legacy.parent != parent or destination.parent != parent:
        raise OSError('User-data migration escapes its parent directory')
    if target_exists:
        destination.rmdir()  # Only the verified empty destination is removed.
    try:
        legacy.rename(destination)
    except OSError:
        if target_exists and not destination.exists():
            destination.mkdir()
        raise
    return destination


# Purpose: Canonical user-data path and lossless legacy profile directory migration
