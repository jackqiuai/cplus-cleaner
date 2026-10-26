"""Launch the single CCCLEANER desktop interface."""
import sys


def main(argv=None):
    from single_instance import run
    arguments = sys.argv[1:] if argv is None else argv
    run(background="--background" in arguments)


if __name__ == "__main__":
    main()
