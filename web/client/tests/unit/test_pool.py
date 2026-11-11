# -------------------------------------------------------------------------
#
#  Part of the CodeChecker project, under the Apache License v2.0 with
#  LLVM Exceptions. See LICENSE for license information.
#  SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# -------------------------------------------------------------------------
"""
Test the Pool that 'CodeChecker store' parses and collects files with.
"""


import atexit
import time
import unittest

from codechecker_common.compatibility.multiprocessing import Pool


def _identity(value):
    return value


def _exit_slowly():
    # Widens the window in which a worker is shutting down, the one in which
    # a SIGTERM escapes into the caller's stack.
    atexit.register(time.sleep, 0.5)


def _workers(executor):
    # multiprocess.pool.Pool on macOS and Windows, ProcessPoolExecutor
    # elsewhere.
    if hasattr(executor, '_pool'):
        return list(executor._pool)
    return list(executor._processes.values())


class PoolTest(unittest.TestCase):

    def test_leaving_the_pool_lets_workers_exit_on_their_own(self):
        """
        A forked worker inherits the CodeChecker SIGTERM handler, which
        raises SystemExit. Terminated while it shuts down, a worker unwinds
        into the caller's stack, and in 'store' runs the cleanup that deletes
        the zip the parent is still writing. Leaving the pool must therefore
        not signal the workers, as ProcessPoolExecutor does not.
        """
        with Pool(max_workers=2, initializer=_exit_slowly) as executor:
            self.assertEqual(list(executor.map(_identity, [1, 2])), [1, 2])
            workers = _workers(executor)

        self.assertTrue(workers)
        for worker in workers:
            worker.join()
            self.assertEqual(worker.exitcode, 0)
