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

from codechecker_report_converter.report import constraint_trace

from codechecker_server.api import what_if


def _trace(steps):
    """
    The stored version 2 trace of `steps`, each step on an analyzer node of
    its own. Each assertion stands for a path condition entry, whose symbol
    it is named by.
    """
    nodes = [{'symbol_ranges': [],
              'variables': step['variables'],
              'path_condition': [{'symbol': line, 'range': ''}
                                 for line in step['smt_assertions']],
              'smt_declarations': step['smt_declarations'],
              'smt_assertions': step['smt_assertions']}
             for step in steps]
    return json.dumps(constraint_trace.encode(
        nodes, [{'kind': step['kind'], 'index': step['index'],
                 'order': step['order'], 'exploded_node': node}
                for node, step in enumerate(steps)]))


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
REASSIGNED = _trace([
    _step(1, [_symbol('v', 'reg_$1')], [DECLARE_1], [V_IS_ZERO]),
    _step(5, [_symbol('v', 'reg_$2')], [DECLARE_1, DECLARE_2], [V_IS_ZERO]),
    _step(7, [_symbol('u', 'reg_$2')], [DECLARE_1, DECLARE_2], [V_IS_ZERO]),
])

# `q` is a pointer parameter assumed null, `n` and `p` hold concrete values,
# and `f` has a symbol whose type has no solver term.
POINTERS = _trace([_step(
    1, [_symbol('q', 'reg_$1', False),
        {'name': 'n', 'value': '0', 'kind': 'integer'},
        {'name': 'p', 'value': '&x', 'kind': 'address'},
        {'name': 'f', 'value': 'reg_$3<float f>', 'kind': 'symbol'}],
    ['(declare-fun reg_$1 () (_ BitVec 64))'],
    ['(assert (= reg_$1 #x0000000000000000))'])])


# `g` holds reg_$1, assumed non-null, until an opaque call at step 3 replaces
# it with reg_$2, which the path assumes null from step 6.
G_NOT_NULL = '(assert (not (= reg_$1 #x00000000)))'
G_NULL = '(assert (= reg_$2 #x00000000))'
OPAQUE_CALL = _trace([
    _step(1, [_symbol('g', 'reg_$1')], [DECLARE_1], [G_NOT_NULL]),
    _step(3, [_symbol('g', 'reg_$2')], [DECLARE_1, DECLARE_2], [G_NOT_NULL]),
    _step(6, [_symbol('g', 'reg_$2')], [DECLARE_1, DECLARE_2],
          [G_NULL, G_NOT_NULL]),
    _step(8, [_symbol('g', 'reg_$2')], [DECLARE_1, DECLARE_2],
          [G_NULL, G_NOT_NULL]),
])

# `p` is first an unconstrained symbol, then holds 0, then &x.
CONCRETE_LATER = _trace([
    _step(1, [_symbol('p', 'reg_$1', False)], [DECLARE_1], []),
    _step(2, [{'name': 'p', 'value': '0', 'kind': 'integer'}], [], []),
    _step(4, [{'name': 'p', 'value': '0', 'kind': 'integer'}], [], []),
    _step(5, [{'name': 'p', 'value': '&x', 'kind': 'address'}], [], []),
])


def _long_trace(length, dies_at):
    """ `n` has one symbol, which the path assumes 0 from step `dies_at`. """
    return _trace([
        _step(order, [_symbol('n', 'reg_$1')], [DECLARE_1],
              [V_IS_ZERO] if order >= dies_at else [])
        for order in range(length)])


def check(trace, *constraints):
    return what_if.check_what_if(
        what_if.PreparedPath(trace),
        [WhatIfConstraint(*constraint) for constraint in constraints])


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
        trace = _trace([_step(
            1, [_symbol('s', 'reg_$1'), _symbol('u', 'reg_$2', False)],
            [DECLARE_1, DECLARE_2],
            ['(assert (= reg_$1 #xffffffff))',
             '(assert (= reg_$2 #xffffffff))'])])

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


class TraceFormatTest(unittest.TestCase):

    def test_older_trace_is_not_checked(self):
        """ A trace stored before version 2 keeps only its version. """
        result = check(json.dumps({'version': 1}), (1, 'v == 0'))
        self.assertEqual(result.verdict, PathVerdict.UNKNOWN)
        self.assertEqual(result.reason, what_if.OLD_TRACE_REASON)
        self.assertEqual(result.baselineReason, what_if.OLD_TRACE_REASON)

    def test_trace_without_smtlib_is_not_checked(self):
        """ What a Clang without Z3 records. """
        trace = constraint_trace.encode(
            [{'symbol_ranges': [],
              'variables': [{'name': 'v', 'value': 'reg_$1<int v>',
                             'kind': 'symbol'}],
              'path_condition': [{'symbol': 'reg_$1<int v>',
                                  'range': '{ [0, 0] }'}]}],
            [{'kind': 'event', 'index': 0, 'order': 1, 'exploded_node': 0}])
        result = check(json.dumps(trace), (1, 'v == 0'))
        self.assertEqual(result.reason, what_if.NO_SMTLIB_REASON)

    def test_unreadable_trace_is_not_checked(self):
        trace = json.loads(REASSIGNED)
        trace['steps'][0]['exploded_node'] = 99
        result = check(json.dumps(trace), (1, 'v == 0'))
        self.assertEqual(result.reason, what_if.UNREADABLE_TRACE_REASON)

    def test_steps_on_one_node_hold_its_values(self):
        """ The edge into a line and its event share their node. """
        trace = json.loads(OPAQUE_CALL)
        trace['steps'].insert(1, {'kind': 'control', 'index': 0, 'order': 2,
                                  'exploded_node': 0})
        trace = json.dumps(trace)
        self.assertEqual(check(trace, (2, 'g == 0')).firstDeadStep, 1)
        self.assertEqual(check(trace, (3, 'g == 0')).verdict,
                         PathVerdict.FEASIBLE)


class PreparedPathTest(unittest.TestCase):

    def setUp(self):
        what_if._prepared_paths.clear()

    def test_checks_on_one_path_answer_as_on_fresh_ones(self):
        checks = [
            (REASSIGNED, [(1, 'v == 1')]),
            (REASSIGNED, [(5, 'v > 3'), (7, 'u < 2')]),
            (REASSIGNED, [(1, 'v ===')]),
            (REASSIGNED, [(2, 'v == 0')]),
            (POINTERS, [(1, 'q != null')]),
            (POINTERS, [(1, 'p == 4096')]),
            (OPAQUE_CALL, [(1, 'g != 0', True)]),
            (CONCRETE_LATER, [(5, 'p == 0', True)]),
            (_long_trace(40, 25), [(39, 'n != 0')]),
        ]
        prepared = {}
        for _ in range(2):
            for trace, constraints in checks:
                path = prepared.setdefault(trace, what_if.PreparedPath(trace))
                self.assertEqual(
                    what_if.check_what_if(
                        path, [WhatIfConstraint(*c) for c in constraints]),
                    check(trace, *constraints))

    def test_baseline_is_solved_once(self):
        path = what_if.PreparedPath(REASSIGNED)
        _RecordingSolver.timeouts = []
        with mock.patch.object(what_if.z3, 'Solver', _RecordingSolver):
            what_if.check_what_if(path, [WhatIfConstraint(1, 'v == 0')])
            what_if.check_what_if(path, [WhatIfConstraint(5, 'v == 1')])
        # Baseline and verdict, then only the verdict.
        self.assertEqual(len(_RecordingSolver.timeouts), 3)

    def test_baseline_the_solver_gave_up_on_is_solved_again(self):
        # Baseline and verdict of two checks.
        answers = iter([z3.unknown, z3.sat, z3.sat, z3.sat])

        class GivesUpOnce(z3.Solver):
            def check(self, *_):
                return next(answers)

            def reason_unknown(self):
                return 'timeout'

        path = what_if.PreparedPath(REASSIGNED)
        with mock.patch.object(what_if.z3, 'Solver', GivesUpOnce):
            first = what_if.check_what_if(path, [WhatIfConstraint(1, 'v ===')])
            second = what_if.check_what_if(path,
                                           [WhatIfConstraint(1, 'v ===')])

        self.assertEqual(first.baselineVerdict, PathVerdict.UNKNOWN)
        self.assertEqual(second.baselineVerdict, PathVerdict.FEASIBLE)

    def test_cache_reads_a_trace_once_per_key(self):
        loads = []

        def load(trace):
            loads.append(trace)
            return trace

        first = what_if.prepared_path('a', lambda: load(REASSIGNED))
        self.assertIs(what_if.prepared_path('a', lambda: load(POINTERS)),
                      first)
        self.assertIsNot(what_if.prepared_path('b', lambda: load(POINTERS)),
                         first)
        self.assertEqual(loads, [REASSIGNED, POINTERS])

    def test_cache_drops_the_least_recently_used_path(self):
        size = what_if.WHAT_IF_CACHE_SIZE
        for key in range(size):
            what_if.prepared_path(key, lambda: REASSIGNED)
        # Key 0 is used again, so key 1 is the one dropped.
        what_if.prepared_path(0, lambda: REASSIGNED)
        what_if.prepared_path(size, lambda: REASSIGNED)

        self.assertEqual(list(what_if._prepared_paths),
                         [*range(2, size), 0, size])


def _frames_trace(nodes, frames, symbols=None):
    """ The stored trace of `nodes`, whole snapshots with their `frame`,
    each node one Path Step whose order is its index plus one. """
    trace = constraint_trace.encode(
        nodes, [{'kind': 'event', 'index': index, 'order': index + 1,
                 'exploded_node': index} for index in range(len(nodes))],
        symbols)
    trace['stack_frames'] = frames
    return json.dumps(trace)


def _node(frame, variables, callers, declarations, assertions):
    return {'frame': frame, 'symbol_ranges': [], 'variables': variables,
            'caller_variables': callers,
            'path_condition': [{'symbol': line, 'range': ''}
                               for line in assertions],
            'smt_declarations': declarations, 'smt_assertions': assertions}


def _integer(name, value):
    return {'name': name, 'value': str(value), 'kind': 'integer'}


def _caller(frame, variable):
    return {'frame': frame, **variable}


TOP_N, TOP_M = _symbol('n', 'reg_$1'), _symbol('m', 'reg_$2')
N_IS_TWO = '(assert (= reg_$1 #x00000002))'

# top(n, m) calls middle(n * 3, m), which calls inner(7): each has an `n`,
# and middle and top hold the same `m`. top's `n` is assumed 2.
CALLERS = _frames_trace([
    _node(0, [TOP_N, TOP_M], [], [DECLARE_1, DECLARE_2], [N_IS_TWO]),
    _node(1, [_integer('n', 6), TOP_M],
          [_caller(0, TOP_N), _caller(0, TOP_M)],
          [DECLARE_1, DECLARE_2], [N_IS_TWO]),
    _node(2, [_integer('n', 7)],
          [_caller(1, _integer('n', 6)), _caller(1, TOP_M),
           _caller(0, TOP_N), _caller(0, TOP_M)],
          [DECLARE_1, DECLARE_2], [N_IS_TWO]),
], [{'function': 'top'},
    {'function': '(anonymous namespace)::middle', 'parent': 0,
     'call_site': {'file': 'f.c', 'line': 20, 'col': 3}},
    {'function': 'inner', 'parent': 1,
     'call_site': {'file': 'f.c', 'line': 12, 'col': 3}}])


class CallerVariablesTest(unittest.TestCase):

    def test_caller_variable_is_written_with_its_function(self):
        self.assertEqual(check(CALLERS, (3, 'top::n == 2')).verdict,
                         PathVerdict.FEASIBLE)
        self.assertEqual(check(CALLERS, (3, 'top::n == 3')).verdict,
                         PathVerdict.INFEASIBLE)
        self.assertEqual(check(CALLERS, (3, 'middle::n != 6')).verdict,
                         PathVerdict.INFEASIBLE)
        self.assertEqual(check(CALLERS, (3, 'inner::n == 7')).verdict,
                         PathVerdict.FEASIBLE)

    def test_name_held_in_several_frames_is_unknown(self):
        result = check(CALLERS, (3, 'n == 7'))
        self.assertEqual(result.verdict, PathVerdict.UNKNOWN)
        self.assertEqual(
            result.reason,
            '`n` names 3 values at this Path Step: `inner::n`, `middle::n` '
            'and `top::n`. Write the one you mean.')
        self.assertEqual(result.reasonConstraintIndex, 0)

    def test_one_value_in_several_frames_is_one_value(self):
        """ middle got top's `m` and holds the same symbol. """
        self.assertEqual(check(CALLERS, (3, 'm > 5')).verdict,
                         PathVerdict.FEASIBLE)

    def test_caller_variable_alone_needs_no_function(self):
        self.assertEqual(check(CALLERS, (1, 'n == 3')).verdict,
                         PathVerdict.INFEASIBLE)

    def test_function_not_on_the_stack_has_no_value(self):
        result = check(CALLERS, (1, 'inner::n == 7'))
        self.assertEqual(result.reason,
                         '`inner::n` has no tracked value at this Path Step.')

    def test_holds_throughout_needs_the_function_when_frames_differ(self):
        result = check(CALLERS, (1, 'n == 2', True))
        self.assertEqual(result.verdict, PathVerdict.UNKNOWN)
        self.assertEqual(
            result.reason,
            '`n` names 2 values at one Path Step of this path: `middle::n` '
            'and `top::n`. Write the one you mean.')
        self.assertEqual(check(CALLERS, (1, 'top::n == 3', True)).verdict,
                         PathVerdict.INFEASIBLE)

    def test_names_offer_every_value_once_or_by_function(self):
        self.assertEqual(
            [name for name, _ in what_if.constrainable_names(
                what_if.PreparedPath(CALLERS), 3)],
            ['inner::n', 'm', 'middle::m', 'middle::n', 'top::m', 'top::n'])
        self.assertEqual(
            what_if.constrainable_names(what_if.PreparedPath(CALLERS), 1),
            [('m', ['symbol:reg_$2<int m>']),
             ('n', ['symbol:reg_$1<int n>']),
             ('top::m', ['symbol:reg_$2<int m>']),
             ('top::n', ['symbol:reg_$1<int n>'])])

    def test_names_throughout_leave_out_names_of_several_frames(self):
        names = [name for name, _ in what_if.constrainable_names(
            what_if.PreparedPath(CALLERS), 1, True)]
        self.assertEqual(names, ['m', 'top::m', 'top::n'])

    def test_recursive_call_says_where_each_was_called(self):
        trace = _frames_trace([
            _node(1, [_integer('n', 1)], [_caller(0, _integer('n', 2))],
                  [], [])],
            [{'function': 'f'},
             {'function': 'f', 'parent': 0,
              'call_site': {'file': 'f.c', 'line': 9, 'col': 3}}])
        self.assertEqual(
            check(trace, (1, 'f::n == 1')).reason,
            '`f::n` names 2 values at this Path Step: `f::n` called at line '
            '9 and `f::n`. Write the one you mean.')

    def test_function_name_the_syntax_cannot_read_is_not_offered(self):
        trace = _frames_trace([_node(0, [_integer('n', 1)], [], [], [])],
                              [{'function': 'max<int>'}])
        self.assertEqual(
            what_if.constrainable_names(what_if.PreparedPath(trace), 1),
            [('n', ['integer:1'])])


LEN_IS_ZERO = '(assert (= reg_$2 #x00000000))'
DECLARE_DERIVED = '(declare-fun derived_$4 () (_ BitVec 32))'
NEW_LEN_NOT_ZERO = '(assert (not (= derived_$4 #x00000000)))'
CONJ = 'conj_$3{int, LC1, S1, #1}'
P = _symbol('p', 'reg_$1', False)

# `p->len` is assumed 0, then `refresh(p)` replaces it with a value assumed
# not 0. `k` is a variable and also memory the table names, holding another
# value. There are no stack frames, as before the analyzer wrote them.
MEMORY = _frames_trace([
    _node(None, [P, _integer('k', 3)], [],
          [DECLARE_1, DECLARE_2], [LEN_IS_ZERO]),
    _node(None, [P, _integer('k', 3)], [],
          [DECLARE_1, DECLARE_2, DECLARE_DERIVED],
          [LEN_IS_ZERO, NEW_LEN_NOT_ZERO]),
], [], {
    'reg_$1<int * p>': {'kind': 'region_value', 'name': 'p',
                        'smt': 'reg_$1', 'smt_signed': False},
    'reg_$2<int Element{SymRegion{reg_$1<int * p>},0 S64b,int}.len>': {
        'kind': 'region_value', 'name': 'p->len', 'smt': 'reg_$2',
        'smt_signed': True},
    CONJ: {'kind': 'conjured', 'smt': 'conj_$3', 'smt_signed': True,
           'origin': {'reason': 'invalidation', 'statement': 'refresh(p)',
                      'function': 'f',
                      'location': {'file': 'f.c', 'line': 5, 'col': 3}}},
    'derived_$4{conj_$3{int, LC1, S1, #1},len}': {
        'kind': 'derived', 'parent': CONJ, 'region': 'p->len',
        'smt': 'derived_$4', 'smt_signed': True},
    'reg_$5<int k>': {'kind': 'region_value', 'name': 'k',
                      'smt': 'reg_$5', 'smt_signed': True},
    'reg_$6<float x>': {'kind': 'region_value', 'name': 'x'}})


class AccessPathTest(unittest.TestCase):

    def test_access_path_reads_as_one_name(self):
        for text, name in [('p->len == 0', 'p->len'),
                           ('*p != null', '*p'),
                           ('a.b[2]->c >= -1', 'a.b[2]->c'),
                           ('Op.Node->ValueList[Op.ResNo].V.SimpleTy==0',
                            'Op.Node->ValueList[Op.ResNo].V.SimpleTy'),
                           ('top::n<3', 'top::n'),
                           ('x->y>3', 'x->y')]:
            self.assertEqual(what_if.parse_constraint(text)[0].variable,
                             name, text)

    def test_memory_name_resolves_to_its_value(self):
        self.assertEqual(check(MEMORY, (1, 'p->len == 0')).verdict,
                         PathVerdict.FEASIBLE)
        self.assertEqual(check(MEMORY, (1, 'p->len != 0')).verdict,
                         PathVerdict.INFEASIBLE)

    def test_memory_with_several_values_is_unknown(self):
        result = check(MEMORY, (2, 'p->len == 0'))
        self.assertEqual(result.verdict, PathVerdict.UNKNOWN)
        self.assertEqual(
            result.reason,
            '`p->len` names 2 values at this Path Step: the one before the '
            'path changed it; and the one after `refresh(p)` may have '
            'changed it, line 5. Holds throughout the path constrains all of '
            'them.')

    def test_holds_throughout_constrains_every_value_of_memory(self):
        result = check(MEMORY, (1, 'p->len == 0', True))
        self.assertEqual(result.verdict, PathVerdict.INFEASIBLE)
        self.assertEqual(result.firstDeadStep, 2)

    def test_variable_hides_memory_of_its_name(self):
        self.assertEqual(check(MEMORY, (1, 'k == 3')).verdict,
                         PathVerdict.FEASIBLE)

    def test_memory_without_a_solver_term_has_no_value(self):
        self.assertIn('no tracked value', check(MEMORY, (1, 'x == 0')).reason)

    def test_names_offer_memory_the_step_declares(self):
        path = what_if.PreparedPath(MEMORY)
        self.assertEqual([name for name, _ in
                          what_if.constrainable_names(path, 1)],
                         ['k', 'p', 'p->len'])
        self.assertEqual([name for name, _ in
                          what_if.constrainable_names(path, 2)], ['k', 'p'])
        self.assertEqual(what_if.constrainable_names(path, 2, True)[2],
                         ('p->len', [
                             'symbol:derived_$4{conj_$3{int, LC1, S1, #1},'
                             'len}',
                             'symbol:reg_$2<int Element{SymRegion{reg_$1<int'
                             ' * p>},0 S64b,int}.len>']))

    def test_no_names_without_solver_input(self):
        trace = json.loads(MEMORY)
        del trace['smtlib']
        self.assertEqual(what_if.constrainable_names(
            what_if.PreparedPath(json.dumps(trace)), 1), [])


def _requires_trace(variables, requirements, declarations, steps=2):
    """
    A path of `steps` steps on one node each, the last holding `variables`
    and the path condition `requirements`, each `(symbol, range, assertion)`
    as Clang writes an entry.
    """
    nodes = [{'symbol_ranges': [], 'variables': variables,
              'path_condition': [{'symbol': symbol, 'range': text}
                                 for symbol, text, _ in requirements],
              'smt_declarations': declarations,
              'smt_assertions': [line for _, _, line in requirements]}
             for _ in range(steps)]
    return json.dumps(constraint_trace.encode(
        nodes, [{'kind': 'event', 'index': index, 'order': index + 1,
                 'exploded_node': index} for index in range(steps)]))


def _declare(name, width):
    return f'(declare-fun {name} () (_ BitVec {width}))'


SIGNED_NEGATIVE = ('reg_$1<int s>', '{ [-2147483648, -1] }',
                   '(assert (and (bvsge reg_$1 #x80000000) '
                   '(bvsle reg_$1 #xffffffff)))')
UNSIGNED_HIGH = ('reg_$2<unsigned int u>', '{ [4294967295, 4294967295] }',
                 '(assert (= reg_$2 #xffffffff))')
SUM_WRAPS = ('(reg_$3<unsigned int m>) + (reg_$4<unsigned int n>)',
             '{ [0, 0] }', '(assert (= (bvadd reg_$3 reg_$4) #x00000000))')
N_NOT_ZERO = ('reg_$4<unsigned int n>', '{ [1, 4294967295] }',
              '(assert (and (bvuge reg_$4 #x00000001) '
              '(bvule reg_$4 #xffffffff)))')
NOT_BELOW = ('(reg_$3<unsigned int m>) >= (reg_$4<unsigned int n>)',
             '{ [0, 0] }',
             '(assert (= (ite (bvuge reg_$3 reg_$4) #x00000001 #x00000000) '
             '#x00000000))')
POINTER_SET = ('reg_$5<int * p>', '{ [1, 18446744073709551615] }',
               '(assert (and (bvuge reg_$5 #x0000000000000001) '
               '(bvule reg_$5 #xffffffffffffffff)))')

EXAMPLE = _requires_trace(
    [_symbol('s', 'reg_$1'), _symbol('m', 'reg_$3', False)],
    [SIGNED_NEGATIVE, UNSIGNED_HIGH, SUM_WRAPS, N_NOT_ZERO, NOT_BELOW,
     POINTER_SET],
    [_declare('reg_$1', 32), _declare('reg_$2', 32), _declare('reg_$3', 32),
     _declare('reg_$4', 32), _declare('reg_$5', 64)])


def check_with_example(trace, *constraints):
    return what_if.check_what_if(
        what_if.PreparedPath(trace),
        [WhatIfConstraint(*constraint) for constraint in constraints], True)


class ExampleTest(unittest.TestCase):

    def test_none_unless_asked(self):
        result = check(EXAMPLE)
        self.assertEqual(result.verdict, PathVerdict.FEASIBLE)
        self.assertIsNone(result.example)
        self.assertIsNone(result.exampleReason)

    def test_one_value_for_each_requirement(self):
        result = check_with_example(EXAMPLE)
        self.assertEqual(result.verdict, PathVerdict.FEASIBLE)
        self.assertIsNone(result.exampleReason)
        values = {value.symbol: value.value for value in result.example}
        self.assertEqual(list(values), [SIGNED_NEGATIVE[0], UNSIGNED_HIGH[0],
                                        SUM_WRAPS[0], N_NOT_ZERO[0],
                                        NOT_BELOW[0], POINTER_SET[0]])

        # In the signedness of the analyzer's range: -1 and 4294967295 are
        # one bit pattern.
        self.assertLess(int(values[SIGNED_NEGATIVE[0]]), 0)
        self.assertEqual(values[UNSIGNED_HIGH[0]], '4294967295')
        self.assertGreater(int(values[POINTER_SET[0]]), 0)

        # An expression has the solver's value of it; a comparison 0 or 1.
        self.assertEqual(values[SUM_WRAPS[0]], '0')
        self.assertEqual(values[NOT_BELOW[0]], '0')
        self.assertGreater(int(values[N_NOT_ZERO[0]]), 0)

    def test_example_satisfies_the_what_if_constraints(self):
        self.assertEqual(
            {value.symbol: value.value for value in check_with_example(
                EXAMPLE, (2, 's == -7')).example}[SIGNED_NEGATIVE[0]],
            '-7')
        result = check_with_example(EXAMPLE, (1, 'm == 5', True))
        values = {value.symbol: value.value for value in result.example}
        # m + n wraps to 0, so n is what is left to 2^32.
        self.assertEqual(values[N_NOT_ZERO[0]], str(2 ** 32 - 5))

    def test_infeasible_path_has_no_example(self):
        result = check_with_example(EXAMPLE, (2, 's == 3'))
        self.assertEqual(result.verdict, PathVerdict.INFEASIBLE)
        self.assertIsNone(result.example)
        self.assertEqual(result.exampleReason,
                         what_if.NOT_FEASIBLE_EXAMPLE_REASON)

    def test_timeout_gives_no_example_and_the_same_verdict(self):
        class TimingOutSolver(z3.Solver):
            def check(self, *_):
                return z3.unknown

            def reason_unknown(self):
                return 'timeout'

        with mock.patch.object(what_if.z3, 'Solver', TimingOutSolver):
            asked = check_with_example(EXAMPLE)
            plain = check(EXAMPLE)

        self.assertEqual((asked.verdict, asked.reason),
                         (plain.verdict, plain.reason))
        self.assertEqual(asked.verdict, PathVerdict.UNKNOWN)
        self.assertIsNone(asked.example)
        self.assertEqual(
            asked.exampleReason,
            'There is no example, because there is no verdict. The solver '
            f'gave up on this path after {what_if.WHAT_IF_SOLVER_TIMEOUT_MS} '
            'ms.')

    def test_asking_costs_no_solver_call(self):
        _RecordingSolver.timeouts = []
        with mock.patch.object(what_if.z3, 'Solver', _RecordingSolver):
            check_with_example(EXAMPLE)
        self.assertEqual(len(_RecordingSolver.timeouts), 2)

    def test_no_solver_input_says_why(self):
        """ What a Clang without Z3 records. """
        trace = constraint_trace.encode(
            [{'symbol_ranges': [], 'variables': [],
              'path_condition': [{'symbol': UNSIGNED_HIGH[0],
                                  'range': UNSIGNED_HIGH[1]}]}],
            [{'kind': 'event', 'index': 0, 'order': 1, 'exploded_node': 0}])
        result = check_with_example(json.dumps(trace))
        self.assertIsNone(result.example)
        self.assertEqual(result.exampleReason,
                         'There is no example, because there is no verdict. '
                         + what_if.NO_SMTLIB_REASON)

    def test_unreadable_constraint_says_why(self):
        result = check_with_example(EXAMPLE, (2, 's =='))
        self.assertIsNone(result.example)
        self.assertEqual(result.exampleReason, 'There is no example, because '
                         'a What-If Constraint cannot be read.')
