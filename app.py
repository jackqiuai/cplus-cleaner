"""Launch the single CCCLEANER desktop interface."""
import sys


def main(argv=None):
    arguments = sys.argv[1:] if argv is None else argv
    if arguments and arguments[0] == '--component-analysis-job':
        from cplus_cleaner.features.system.system_analysis_helper import main as analyze
        return analyze(arguments[1:])
    if arguments and arguments[0] == '--smoke-test':
        if len(arguments) != 2:
            raise SystemExit(2)
        from cplus_cleaner.application.bundle_check import main as check_bundle
        return check_bundle(arguments[1])
    from cplus_cleaner.application.single_instance import run
    run(background="--background" in arguments)


if __name__ == "__main__":
    raise SystemExit(main())

# Purpose: Desktop, background and read-only analysis dispatch
