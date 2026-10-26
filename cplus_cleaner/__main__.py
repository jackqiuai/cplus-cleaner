"""Run the desktop with ``python -m cplus_cleaner``."""
import sys
from cplus_cleaner.application.single_instance import run


if __name__ == '__main__':
    run(background='--background' in sys.argv[1:])
