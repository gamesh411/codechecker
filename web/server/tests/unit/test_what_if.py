# -------------------------------------------------------------------------
#
#  Part of the CodeChecker project, under the Apache License v2.0 with
#  LLVM Exceptions. See LICENSE for license information.
#  SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# -------------------------------------------------------------------------

""" What-If evaluation on hand-written constraint traces. """


import json
import time
import unittest
from unittest import mock

from codechecker_api.python.DBAccess_v6.ttypes import PathVerdict, \
    WhatIfConstraint

from codechecker_server.api import what_if


def _step(order, variables, declarations, assertions):
    return {'kind': 'event', 'index': order, 'order': order,
            'variables': variables,
            'smt_declarations': declarations,
            'smt_assertions': assertions}


def _symbol(name, smt, signed=True):
    return {'name': name, 'value': smt + '<int ' + name + '>',
            'kind': 'symbol', 'smt': smt, 'smt_signed': signed}


DECLARE_1 = '(declare-fun reg_$1 () (_ BitVec 32))'
DECLARE_2 = '(declare-fun reg_$2 () (_ BitVec 32))'
V_IS_ZERO = '(assert (= reg_$1 #x00000000))'

# `v` holds reg_$1, assumed 0, until a call replaces it with an unconstrained
# reg_$2. The last step holds the whole path condition.
REASSIGNED = json.dumps({'version': 1, 'steps': [
    _step(1, [_symbol('v', 'reg_$1')], [DECLARE_1], [V_IS_ZERO]),
    _step(5, [_symbol('v', 'reg_$2')], [DECLARE_1, DECLARE_2], [V_IS_ZERO]),
    _step(7, [], [DECLARE_1], [V_IS_ZERO]),
]})


def check(trace, *constraints):
    return what_if.check_what_if(
        trace, [WhatIfConstraint(step, text) for step, text in constraints])


class WhatIfTest(unittest.TestCase):

    def test_variable_means_its_value_at_the_anchored_step(self):
        self.assertEqual(
            check(REASSIGNED, (1, 'v == 1')).verdict, PathVerdict.INFEASIBLE)
        self.assertEqual(
            check(REASSIGNED, (1, 'v == 0')).verdict, PathVerdict.FEASIBLE)
        self.assertEqual(
            check(REASSIGNED, (5, 'v == 1')).verdict, PathVerdict.FEASIBLE)

    def test_signedness_decides_the_comparison(self):
        trace = json.dumps({'version': 1, 'steps': [_step(
            1, [_symbol('s', 'reg_$1'), _symbol('u', 'reg_$2', False)],
            [DECLARE_1, DECLARE_2],
            ['(assert (= reg_$1 #xffffffff))',
             '(assert (= reg_$2 #xffffffff))'])]})

        self.assertEqual(check(trace, (1, 's < 0')).verdict,
                         PathVerdict.FEASIBLE)
        self.assertEqual(check(trace, (1, 'u < 0')).verdict,
                         PathVerdict.INFEASIBLE)
        self.assertEqual(check(trace, (1, 'u >= 4294967295')).verdict,
                         PathVerdict.FEASIBLE)

    def test_unreadable_input_is_unknown_with_a_reason(self):
        for text, reason in [
                ('v = 0', 'Cannot read the constraint: Expected one of'),
                ('v == 0 0', 'Cannot read the constraint: Unexpected `0`.'),
                ('', 'Cannot read the constraint: Expected `variable')]:
            result = check(REASSIGNED, (1, text))
            self.assertEqual(result.verdict, PathVerdict.UNKNOWN, text)
            self.assertIn(reason, result.reason)
            self.assertEqual(result.baselineVerdict, PathVerdict.FEASIBLE)

    def test_value_outside_the_type_is_unknown(self):
        result = check(REASSIGNED, (1, 'v == 4294967296'))
        self.assertEqual(result.verdict, PathVerdict.UNKNOWN)
        self.assertIn('outside the range of `v`', result.reason)

    def test_unknown_variable_is_named(self):
        result = check(REASSIGNED, (1, 'w == 0'))
        self.assertEqual(result.verdict, PathVerdict.UNKNOWN)
        self.assertEqual(result.reason,
                         '`w` has no tracked value at this Path Step.')

    def test_solver_timeout_gives_unknown(self):
        """
        Factoring a 64-bit semiprime takes Z3 far longer than the timeout.
        """
        trace = json.dumps({'version': 1, 'steps': [_step(
            1, [], ['(declare-fun x () (_ BitVec 64))',
                    '(declare-fun y () (_ BitVec 64))'],
            ['(assert (bvugt x #x0000000000000001))',
             '(assert (bvugt y #x0000000000000001))',
             '(assert (bvult x #x00000000ffffffff))',
             '(assert (bvult y #x00000000ffffffff))',
             '(assert (= (bvmul x y) #xfffffffa00000005))'])]})

        with mock.patch.object(what_if, 'WHAT_IF_SOLVER_TIMEOUT_MS', 50):
            start = time.monotonic()
            result = check(trace)
            elapsed = time.monotonic() - start

        self.assertEqual(result.baselineVerdict, PathVerdict.UNKNOWN)
        self.assertEqual(result.baselineReason,
                         'The solver gave up on this path after 50 ms.')
        self.assertLess(elapsed, 2)

    def test_timeout_is_short(self):
        self.assertLessEqual(what_if.WHAT_IF_SOLVER_TIMEOUT_MS, 5000)
