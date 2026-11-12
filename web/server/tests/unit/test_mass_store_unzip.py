# -------------------------------------------------------------------------
#
#  Part of the CodeChecker project, under the Apache License v2.0 with
#  LLVM Exceptions. See LICENSE for license information.
#  SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# -------------------------------------------------------------------------
""" Unpacking the ZIP that 'CodeChecker store' sends. """

import base64
import io
import os
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path

from codechecker_server.api.mass_store_run import unzip


def _store_zip(files: dict[str, bytes]) -> str:
    """ The ZIP as the client sends it: compressed, then Base64. """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return base64.b64encode(
        zlib.compress(buffer.getvalue(), zlib.Z_BEST_COMPRESSION)).decode()


class UnzipTest(unittest.TestCase):

    def test_unpacks_a_zip_larger_than_one_piece(self):
        """ The ZIP is decompressed piece by piece; a file whose compressed
        and decompressed sizes both span several pieces comes out whole. """
        files = {'reports/a.plist': os.urandom(3 << 20),
                 'reports/b.plist': b'x' * (40 << 20),
                 'root/src/c.c': b'int main() {}\n'}
        with tempfile.TemporaryDirectory() as out:
            size = unzip('run', _store_zip(files), Path(out))
            for name, content in files.items():
                self.assertEqual(Path(out, name).read_bytes(), content)
        self.assertGreater(size, sum(map(len, files.values())))

    def test_nothing_to_unpack(self):
        with tempfile.TemporaryDirectory() as out:
            self.assertEqual(unzip('run', '', Path(out)), 0)
