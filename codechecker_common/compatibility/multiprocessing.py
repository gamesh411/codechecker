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
from functools import partial
from typing import Any, Callable, Iterable, Optional, TypeVar

# pylint: disable=no-name-in-module
# pylint: disable=unused-import

# Import the platform-specific implementations
if sys.platform in ["darwin", "win32"]:
    from multiprocess import Pool as _MultiprocessPool  # type: ignore
    from multiprocess import cpu_count
else:
    from concurrent.futures import ProcessPoolExecutor  # type: ignore
    from multiprocessing import cpu_count


# Create a wrapper class for Pool to ensure consistent parameter naming
class Pool:
    """Compatibility wrapper for multiprocessing Pool.

    This wrapper ensures consistent parameter naming across platforms.
    On Linux, it wraps ProcessPoolExecutor and translates 'processes' to 'max_workers'.
    On macOS and Windows, it wraps multiprocess.Pool directly.
    """

    def __init__(self, processes: Optional[int] = None):
        """Initialize the Pool with the given number of processes.

        Args:
            processes: The number of worker processes to use. If None, uses the
                number of CPU cores.
        """
        if sys.platform in ["darwin", "win32"]:
            self._pool = _MultiprocessPool(processes=processes)
        else:
            self._pool = ProcessPoolExecutor(max_workers=processes)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._pool.__exit__(exc_type, exc_val, exc_tb)

    T = TypeVar("T")
    U = TypeVar("U")

    def map(
        self, func: Callable[[T], U], iterable: Iterable[T], *args: Any, **kwargs: Any
    ) -> Iterable[U]:
        """Map function to each element in the iterable.

        This handles the differences between multiprocess.Pool.map and
        ProcessPoolExecutor.map across platforms.

        Args:
            func: The function to apply to each element
            iterable: An iterable of items to process

        Returns:
            An iterable of results
        """
        if sys.platform in ["darwin", "win32"]:
            return self._pool.map(func, iterable, *args, **kwargs)
        else:
            # ProcessPoolExecutor.map doesn't support additional args, so we use partial
            if args or kwargs:
                wrapped_func = partial(func, *args, **kwargs)
                return self._pool.map(wrapped_func, iterable)
            return self._pool.map(func, iterable)
