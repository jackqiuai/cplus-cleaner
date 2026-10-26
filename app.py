"""Launch the single CCCLEANER desktop interface."""
import sys


def main(argv=None):
    arguments = sys.argv[1:] if argv is None else argv
    if arguments and arguments[0] == '--component-analysis-job':
        from cplus_cleaner.features.system.system_analysis_helper import main as analyze
        return analyze(arguments[1:])
    from cplus_cleaner.application.single_instance import run
    run(background="--background" in arguments)


if __name__ == "__main__":
    main()
