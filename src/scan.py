"""Thin launcher for the qeeqbox social-analyzer CLI with a configurable worker count.

The scanner probes sites from a thread pool of 15 workers, hardcoded, and offers no
flag to change it. All 999 sites take about 7 minutes that way and about 2 minutes
with 60 workers. This module imports the scanner, raises the worker count, and hands
the remaining arguments to its own CLI unchanged, so the actor can keep running it as
a separate process (killable when the time limit is reached) and parse its JSON.

    python -m src.scan --workers 60 --username elonmusk --websites "<url patterns>" ...
"""
import importlib
import sys
import warnings


def main() -> None:
    argv = sys.argv[1:]
    workers = 60
    if '--workers' in argv:
        i = argv.index('--workers')
        workers = int(argv[i + 1])
        del argv[i:i + 2]
    warnings.simplefilter('ignore')          # bs4 encoding and XML-as-HTML chatter on stderr
    scanner = importlib.import_module('social-analyzer')
    sa = scanner.SocialAnalyzer()
    sa.workers = max(1, min(workers, 100))
    sys.argv = ['social-analyzer'] + argv
    sa.run_as_cli()


if __name__ == '__main__':
    main()
