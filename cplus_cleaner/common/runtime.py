"""Resource locations and self-launch commands for Python and frozen Windows builds."""
from pathlib import Path
import sys


def frozen():
    return bool(getattr(sys, 'frozen', False))


def resource_root():
    if frozen():
        return Path(sys._MEIPASS).resolve()
    return Path(__file__).resolve().parents[2]


def application_root():
    return Path(sys.executable).resolve().parent if frozen() else resource_root()


def protected_paths(resources=None):
    roots = [Path(resources) if resources is not None else resource_root()]
    if frozen():
        roots.append(Path(sys.executable).resolve())
    return roots


def launch_command(*arguments):
    executable = Path(sys.executable)
    if frozen():
        return [str(executable), *map(str, arguments)]
    windowed = executable.with_name('pythonw.exe')
    if windowed.is_file():
        executable = windowed
    return [str(executable), str(resource_root()/'app.py'), *map(str, arguments)]

# Purpose: Source and frozen resource paths and self-launch commands
