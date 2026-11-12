# -------------------------------------------------------------------------
#
#  Part of the CodeChecker project, under the Apache License v2.0 with
#  LLVM Exceptions. See LICENSE for license information.
#  SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# -------------------------------------------------------------------------
"""
Multiprocessing compatibility module.
"""
import sys

# pylint: disable=no-name-in-module
# pylint: disable=unused-import
if sys.platform in ["darwin", "win32"]:
    from multiprocess import (  # type: ignore
        Pipe, Process,
        Queue,
        Value,
        cpu_count
    )
    from multiprocess.managers import SyncManager  # type: ignore
    from multiprocess.pool import Pool as _Pool  # type: ignore

    class _Starred:
        """ Calls the function with one tuple of arguments spread out. """
        def __init__(self, func):
            self.func = func

        def __call__(self, args):
            return self.func(*args)

    # The base's __reduce__ raises on purpose: pools cannot be pickled.
    class Pool(_Pool):  # type: ignore # pylint: disable=abstract-method
        """
        Offers the ProcessPoolExecutor interface that the other platforms use,
        so callers can use 'max_workers' and a multi-iterable 'map'.
        """
        def __init__(self, max_workers=None, **kwargs):
            super().__init__(processes=max_workers, **kwargs)

        def map(self, func, *iterables, **kwargs):  # type: ignore
            # Results come in order as they are ready, as from
            # ProcessPoolExecutor.map, rather than all at once: a caller that
            # handles one at a time, as 'store' does with parsed report
            # files, then never holds them all.
            return super().imap(_Starred(func), zip(*iterables), **kwargs)

        def __exit__(self, exc_type, exc_val, exc_tb):
            # Waits like ProcessPoolExecutor instead of terminating. A forked
            # worker inherits the caller's SIGTERM handler, which raises
            # SystemExit; a SIGTERM during the worker's exit unwinds it into
            # the caller's stack, where it runs the caller's cleanup, such as
            # the 'store' finally block that deletes the zip being written.
            self.close()
            self.join()
else:
    from concurrent.futures import ProcessPoolExecutor as Pool
    from multiprocessing import (
        Pipe,
        Process,
        Queue,
        Value,
        cpu_count
    )
    from multiprocessing.managers import SyncManager
