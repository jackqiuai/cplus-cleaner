"""Windowless elevated entry point for the single read-only DISM analysis."""
import sys
from system_analysis import helper_main


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit(2)
    helper_main(sys.argv[1])
