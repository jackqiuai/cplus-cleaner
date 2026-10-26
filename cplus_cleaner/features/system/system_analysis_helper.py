"""Windowless elevated entry point for the single read-only DISM analysis."""
import sys
from cplus_cleaner.features.system.system_analysis import helper_main


def main(argv=None):
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1:
        raise SystemExit(2)
    helper_main(arguments[0])


if __name__ == '__main__':
    main()

# Purpose: Read-only component-analysis helper entry
