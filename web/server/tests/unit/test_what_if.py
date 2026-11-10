# -------------------------------------------------------------------------
#
#  Part of the CodeChecker project, under the Apache License v2.0 with
#  LLVM Exceptions. See LICENSE for license information.
#  SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# -------------------------------------------------------------------------

""" What-If evaluation on hand-written constraint traces. """


import json
import unittest
from unittest import mock

import z3

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
    _step(7, [_symbol('u', 'reg_$2')], [DECLARE_1, DECLARE_2], [V_IS_ZERO]),
]})

# `q` is a pointer parameter assumed null, `n` and `p` hold concrete values,
# and `f` has a symbol whose type has no solver term.
POINTERS = json.dumps({'version': 1, 'steps': [_step(
    1, [_symbol('q', 'reg_$1', False),
        {'name': 'n', 'value': '0', 'kind': 'integer'},
        {'name': 'p', 'value': '&x', 'kind': 'address'},
        {'name': 'f', 'value': 'reg_$3<float f>', 'kind': 'symbol'}],
    ['(declare-fun reg_$1 () (_ BitVec 64))'],
    ['(assert (= reg_$1 #x0000000000000000))'])]})


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

    def test_terms_joined_by_and_all_hold(self):
        self.assertEqual(check(REASSIGNED, (1, 'v == 0 && v != 1')).verdict,
                         PathVerdict.FEASIBLE)
        self.assertEqual(check(REASSIGNED, (5, 'v > 3 && v < 2')).verdict,
                         PathVerdict.INFEASIBLE)

    def test_constraints_on_several_steps_apply_together(self):
        """
        Each alone keeps the path feasible; `v` at step 5 and `u` at step 7
        are one value, which cannot be both above 3 and below 2.
        """
        self.assertEqual(check(REASSIGNED, (5, 'v > 3')).verdict,
                         PathVerdict.FEASIBLE)
        self.assertEqual(check(REASSIGNED, (7, 'u < 2')).verdict,
                         PathVerdict.FEASIBLE)
        self.assertEqual(
            check(REASSIGNED, (5, 'v > 3'), (7, 'u < 2')).verdict,
            PathVerdict.INFEASIBLE)
        self.assertEqual(
            check(REASSIGNED, (1, 'v == 0'), (7, 'u < 2')).verdict,
            PathVerdict.FEASIBLE)

    def test_null_is_zero(self):
        self.assertEqual(check(POINTERS, (1, 'q == null')).verdict,
                         PathVerdict.FEASIBLE)
        self.assertEqual(check(POINTERS, (1, 'q != null')).verdict,
                         PathVerdict.INFEASIBLE)

    def test_concrete_integer_is_compared_directly(self):
        for text, verdict in [('n == 0', PathVerdict.FEASIBLE),
                              ('n == null', PathVerdict.FEASIBLE),
                              ('n != 0', PathVerdict.INFEASIBLE),
                              ('n > -1', PathVerdict.FEASIBLE),
                              ('n >= 1', PathVerdict.INFEASIBLE)]:
            self.assertEqual(check(POINTERS, (1, text)).verdict, verdict,
                             text)

    def test_concrete_address_is_not_null(self):
        """ `p` holds &x, which no comparison with 0 can make null. """
        for text, verdict in [('p == 0', PathVerdict.INFEASIBLE),
                              ('p == null', PathVerdict.INFEASIBLE),
                              ('p != null', PathVerdict.FEASIBLE),
                              ('p > 0', PathVerdict.FEASIBLE),
                              ('p <= 0', PathVerdict.INFEASIBLE)]:
            self.assertEqual(check(POINTERS, (1, text)).verdict, verdict,
                             text)

    def test_address_against_a_number_is_unknown(self):
        result = check(POINTERS, (1, 'p == 4096'))
        self.assertEqual(result.verdict, PathVerdict.UNKNOWN)
        self.assertEqual(
            result.reason,
            '`p` holds &x, whose numeric value is not recorded, so only a '
            'comparison with 0 or null can be decided.')

    def test_symbol_without_solver_term_is_unknown(self):
        result = check(POINTERS, (1, 'f == 0'))
        self.assertEqual(result.verdict, PathVerdict.UNKNOWN)
        self.assertEqual(
            result.reason,
            '`f` holds reg_$3<float f>, whose type the check cannot '
            'reason about.')

    def test_syntax_error_is_reported_apart_from_verdicts(self):
        for text, message, offset in [
                ('v = 0', 'Expected an operator (`==`, `!=`, `<`, `<=`, '
                          '`>`, `>=`), found `=`.', 2),
                ('v == 0 0', 'Expected `&&` or the end, found `0`.', 7),
                ('v == 0 &&', 'Expected a variable name at the end.', 9),
                ('v == x', 'Expected an integer or null, found `x`.', 5),
                ('', 'Expected `variable op value`.', 0)]:
            result = check(REASSIGNED, (1, text))
            self.assertEqual(result.verdict, PathVerdict.UNKNOWN, text)
            self.assertIsNotNone(result.syntaxError, text)
            self.assertEqual(result.syntaxError.constraintIndex, 0, text)
            self.assertEqual(result.syntaxError.message, message, text)
            self.assertEqual(result.syntaxError.offset, offset, text)
            self.assertEqual(result.baselineVerdict, PathVerdict.FEASIBLE)

    def test_syntax_error_names_its_constraint(self):
        result = check(REASSIGNED, (1, 'v == 0'), (5, 'v ==='), (7, 'u <'))
        self.assertEqual(result.syntaxError.constraintIndex, 1)

    def test_syntax_error_comes_first_without_solver_input(self):
        """ A typo is still pointed out where nothing can be solved. """
        result = check(None, (1, 'v =='))
        self.assertEqual(result.syntaxError.message,
                         'Expected an integer or null at the end.')
        self.assertEqual(result.baselineReason, what_if.NO_TRACE_REASON)

    def test_readable_constraints_have_no_syntax_error(self):
        result = check(REASSIGNED, (1, 'w == 0'))
        self.assertIsNone(result.syntaxError)

    def test_unknown_reason_names_its_constraint(self):
        result = check(REASSIGNED, (1, 'v == 0'), (5, 'w == 0'))
        self.assertEqual(result.reasonConstraintIndex, 1)
        self.assertIsNone(check(REASSIGNED, (1, 'v == 0')).
                          reasonConstraintIndex)

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
        Z3 is made to time out on every call, whatever the machine's speed,
        and every call must have been given the timeout.
        """
        timeouts = []

        class TimingOutSolver(z3.Solver):
            def set(self, *args, **kwargs):
                if args[:1] == ('timeout',):
                    timeouts.append(args[1])
                super().set(*args, **kwargs)

            def check(self, *_):
                return z3.unknown

            def reason_unknown(self):
                return 'timeout'

        with mock.patch.object(what_if.z3, 'Solver', TimingOutSolver):
            result = check(REASSIGNED, (1, 'v == 0'))

        reason = (f'The solver gave up on this path after '
                  f'{what_if.WHAT_IF_SOLVER_TIMEOUT_MS} ms.')
        self.assertEqual(result.baselineVerdict, PathVerdict.UNKNOWN)
        self.assertEqual(result.baselineReason, reason)
        self.assertEqual(result.verdict, PathVerdict.UNKNOWN)
        self.assertEqual(result.reason, reason)
        self.assertEqual(timeouts, [what_if.WHAT_IF_SOLVER_TIMEOUT_MS] * 2)

    def test_timeout_is_short(self):
        self.assertLessEqual(what_if.WHAT_IF_SOLVER_TIMEOUT_MS, 5000)
