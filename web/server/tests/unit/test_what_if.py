# -------------------------------------------------------------------------
#
#  Part of the CodeChecker project, under the Apache License v2.0 with
#  LLVM Exceptions. See LICENSE for license information.
#  SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# -------------------------------------------------------------------------

""" What-If evaluation on hand-written constraint traces. """


import json
import math
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


# `g` holds reg_$1, assumed non-null, until an opaque call at step 3 replaces
# it with reg_$2, which the path assumes null from step 6.
G_NOT_NULL = '(assert (not (= reg_$1 #x00000000)))'
G_NULL = '(assert (= reg_$2 #x00000000))'
OPAQUE_CALL = json.dumps({'version': 1, 'steps': [
    _step(1, [_symbol('g', 'reg_$1')], [DECLARE_1], [G_NOT_NULL]),
    _step(3, [_symbol('g', 'reg_$2')], [DECLARE_1, DECLARE_2], [G_NOT_NULL]),
    _step(6, [_symbol('g', 'reg_$2')], [DECLARE_1, DECLARE_2],
          [G_NULL, G_NOT_NULL]),
    _step(8, [_symbol('g', 'reg_$2')], [DECLARE_1, DECLARE_2],
          [G_NULL, G_NOT_NULL]),
]})

# `p` is first an unconstrained symbol, then holds 0, then &x.
CONCRETE_LATER = json.dumps({'version': 1, 'steps': [
    _step(1, [_symbol('p', 'reg_$1', False)], [DECLARE_1], []),
    _step(2, [{'name': 'p', 'value': '0', 'kind': 'integer'}], [], []),
    _step(4, [{'name': 'p', 'value': '0', 'kind': 'integer'}], [], []),
    _step(5, [{'name': 'p', 'value': '&x', 'kind': 'address'}], [], []),
]})


def _long_trace(length, dies_at):
    """ `n` has one symbol, which the path assumes 0 from step `dies_at`. """
    return json.dumps({'version': 1, 'steps': [
        _step(order, [_symbol('n', 'reg_$1')], [DECLARE_1],
              [V_IS_ZERO] if order >= dies_at else [])
        for order in range(length)]})


def check(trace, *constraints):
    return what_if.check_what_if(
        trace, [WhatIfConstraint(*constraint) for constraint in constraints])


class _RecordingSolver(z3.Solver):
    """ Records the timeout of every solver call. """
    timeouts = []

    def set(self, *args, **kwargs):
        if args[:1] == ('timeout',):
            _RecordingSolver.timeouts.append(args[1])
        super().set(*args, **kwargs)


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


class HoldsThroughoutTest(unittest.TestCase):

    def test_off_constrains_only_the_anchored_value(self):
        self.assertEqual(check(OPAQUE_CALL, (1, 'g != 0')).verdict,
                         PathVerdict.FEASIBLE)
        self.assertEqual(check(OPAQUE_CALL, (1, 'g != 0', False)).verdict,
                         PathVerdict.FEASIBLE)

    def test_on_constrains_every_value_of_the_variable(self):
        result = check(OPAQUE_CALL, (1, 'g != 0', True))
        self.assertEqual(result.verdict, PathVerdict.INFEASIBLE)
        self.assertEqual(result.baselineVerdict, PathVerdict.FEASIBLE)

    def test_on_ignores_which_step_it_was_typed_at(self):
        for step in (1, 3, 8):
            self.assertEqual(
                check(OPAQUE_CALL, (step, 'g != 0', True)).firstDeadStep, 6,
                step)

    def test_on_holds_where_the_variable_matches_it(self):
        self.assertEqual(check(REASSIGNED, (5, 'v == 0', True)).verdict,
                         PathVerdict.FEASIBLE)

    def test_variable_missing_at_the_anchor_is_found_elsewhere(self):
        """ `u` exists only at step 7 and is the same value `v` had. """
        self.assertEqual(
            check(REASSIGNED, (1, 'u > 3', True), (5, 'v < 2')).verdict,
            PathVerdict.INFEASIBLE)

    def test_variable_without_a_value_anywhere_is_unknown(self):
        result = check(REASSIGNED, (1, 'v == 0'), (1, 'w == 0', True))
        self.assertEqual(result.verdict, PathVerdict.UNKNOWN)
        self.assertEqual(result.reason,
                         '`w` has no tracked value at any Path Step.')
        self.assertEqual(result.reasonConstraintIndex, 1)

    def test_concrete_values_are_decided_where_the_variable_holds_them(self):
        """ A symbol at one step and concrete values at others all count. """
        for text, verdict, dead in [('p != null', PathVerdict.INFEASIBLE, 2),
                                    ('p == null', PathVerdict.INFEASIBLE, 5),
                                    ('p >= 0', PathVerdict.FEASIBLE, None)]:
            result = check(CONCRETE_LATER, (1, text, True))
            self.assertEqual(result.verdict, verdict, text)
            self.assertEqual(result.firstDeadStep, dead, text)

    def test_address_against_a_number_throughout_is_unknown(self):
        result = check(CONCRETE_LATER, (1, 'p == 4096', True))
        self.assertEqual(result.verdict, PathVerdict.UNKNOWN)
        self.assertIn('`p` holds &x', result.reason)


class FirstDeadStepTest(unittest.TestCase):

    def test_only_an_infeasible_verdict_names_a_step(self):
        self.assertIsNone(check(OPAQUE_CALL, (1, 'g != 0')).firstDeadStep)
        self.assertIsNone(check(OPAQUE_CALL, (1, 'w == 0')).firstDeadStep)
        self.assertIsNone(check(OPAQUE_CALL, (1, 'g ==')).firstDeadStep)
        self.assertIsNone(
            check(OPAQUE_CALL, (1, 'g != 0')).firstDeadStepIsEarliest)

    def test_dead_step_can_come_before_the_anchor(self):
        """ reg_$2 is assumed null from step 6, before the anchor at 8. """
        result = check(OPAQUE_CALL, (8, 'g != 0'))
        self.assertEqual(result.verdict, PathVerdict.INFEASIBLE)
        self.assertEqual(result.firstDeadStep, 6)
        self.assertTrue(result.firstDeadStepIsEarliest)

    def test_anchored_term_applies_from_where_its_symbol_appears(self):
        """
        A term that contradicts itself kills the path where its value first
        appears, not at the start of the path and not at the anchor.
        """
        self.assertEqual(
            check(OPAQUE_CALL, (8, 'g > 3 && g < 2')).firstDeadStep, 3)
        self.assertEqual(
            check(OPAQUE_CALL, (1, 'g > 3 && g < 2')).firstDeadStep, 1)

    def test_anchored_concrete_value_applies_from_where_it_is_held(self):
        """ `p` holds 0 from step 2 up to the anchor at step 4. """
        self.assertEqual(
            check(CONCRETE_LATER, (4, 'p != 0')).firstDeadStep, 2)
        self.assertEqual(
            check(CONCRETE_LATER, (5, 'p == 0')).firstDeadStep, 5)

    def test_path_condition_decides_where_the_path_dies(self):
        self.assertEqual(
            check(_long_trace(40, 25), (39, 'n != 0')).firstDeadStep, 25)
        self.assertEqual(
            check(_long_trace(40, 0), (39, 'n != 0')).firstDeadStep, 0)
        self.assertEqual(
            check(_long_trace(40, 39), (0, 'n != 0')).firstDeadStep, 39)

    def test_search_takes_logarithmically_many_solver_calls(self):
        _RecordingSolver.timeouts = []
        with mock.patch.object(what_if.z3, 'Solver', _RecordingSolver):
            result = check(_long_trace(100, 61), (99, 'n != 0'))

        self.assertEqual(result.firstDeadStep, 61)
        self.assertTrue(result.firstDeadStepIsEarliest)
        # Baseline, verdict, then a binary search over the other 99 steps.
        self.assertLessEqual(len(_RecordingSolver.timeouts),
                             2 + math.ceil(math.log2(100)))
        self.assertTrue(all(timeout <= what_if.WHAT_IF_SOLVER_TIMEOUT_MS
                            for timeout in _RecordingSolver.timeouts))

    def test_search_stops_when_its_budget_is_spent(self):
        """
        Each search call may use only what is left of the budget, and once it
        is spent the latest step proven dead is named.
        """
        clock = iter(range(0, 10 ** 6, 1500))
        _RecordingSolver.timeouts = []
        with mock.patch.object(what_if.z3, 'Solver', _RecordingSolver), \
                mock.patch.object(what_if.time, 'monotonic',
                                  lambda: next(clock) / 1000):
            result = check(_long_trace(100, 61), (99, 'n != 0'))

        self.assertEqual(result.verdict, PathVerdict.INFEASIBLE)
        search = _RecordingSolver.timeouts[2:]
        self.assertTrue(search)
        self.assertLessEqual(sum(search), what_if.WHAT_IF_SEARCH_BUDGET_MS)
        self.assertGreaterEqual(result.firstDeadStep, 61)
        self.assertFalse(result.firstDeadStepIsEarliest)

    def test_solver_giving_up_in_the_search_counts_as_alive(self):
        """ The named step is always one the solver proved dead. """
        calls = []

        class GivesUpInTheSearch(z3.Solver):
            def check(self, *args):
                calls.append(None)
                # The baseline and verdict calls come first.
                return super().check(*args) if len(calls) <= 2 \
                    else z3.unknown

        with mock.patch.object(what_if.z3, 'Solver', GivesUpInTheSearch):
            result = check(_long_trace(10, 3), (9, 'n != 0'))

        self.assertEqual(result.verdict, PathVerdict.INFEASIBLE)
        self.assertEqual(result.firstDeadStep, 9)
        self.assertFalse(result.firstDeadStepIsEarliest)
