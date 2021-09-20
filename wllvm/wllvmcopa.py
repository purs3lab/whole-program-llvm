#!/usr/bin/env python
"""This is a wrapper around the real compiler.

Here, our goal is to remove all the optimization flags
and add only the require optimizations.
"""

import sys

from .compilers import wcompilecopa


def main():
    """ The entry point to wllvm.
    """
    return wcompilecopa("copa")


if __name__ == '__main__':
    sys.exit(main())
