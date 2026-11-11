# -------------------------------------------------------------------------
#
#  Part of the CodeChecker project, under the Apache License v2.0 with
#  LLVM Exceptions. See LICENSE for license information.
#  SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# -------------------------------------------------------------------------
"""
Thrift protocol limits shared by the server and its Python clients.
"""

# The longest string a Thrift JSON message may hold, in characters. None
# means any length.
#
# Thrift 0.25 defaults to 16,384,000, which a legitimate message
# exceeds: the base64 ZIP of a store compressed to more than about 12.3 MiB,
# or a report whose constraint trace is longer. Such a limit bounds no
# memory: the server holds a request's whole body before it decodes it, and
# TJSONProtocol has no limit on containers, so a peer can send any number of
# strings anyway. What a store may unpack is limited by the server instead.
STRING_LENGTH_LIMIT = None
