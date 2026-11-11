# -------------------------------------------------------------------------
#
#  Part of the CodeChecker project, under the Apache License v2.0 with
#  LLVM Exceptions. See LICENSE for license information.
#  SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# -------------------------------------------------------------------------
"""
What-If evaluation: checks a report's Bug Path against temporary assumptions
of the reader, using the SMT-LIB that Clang records in the Constraint Snapshot
of each Path Step.

Every verdict is about the reported Bug Path only. The bug may still be
reachable through another path, which would need a new analysis to find.
"""

import json
import re
import time
from collections import OrderedDict
from dataclasses import dataclass

import z3

from codechecker_api.python.DBAccess_v6.ttypes import PathVerdict, \
    WhatIfResult, WhatIfSyntaxError

from codechecker_report_converter.report.constraint_trace import \
    ConstraintTrace, MalformedTrace, VERSION as TRACE_VERSION

from codechecker_common.logger import get_logger

LOG = get_logger('server')

# Per solver call. A check makes two calls, the Baseline Verdict and the one
# with the reader's constraints, and an Infeasible one then searches for the
# first dead Path Step within WHAT_IF_SEARCH_BUDGET_MS. So an API worker is
# held at most 2 * WHAT_IF_SOLVER_TIMEOUT_MS + WHAT_IF_SEARCH_BUDGET_MS.
WHAT_IF_SOLVER_TIMEOUT_MS = 2000
WHAT_IF_SEARCH_BUDGET_MS = 2 * WHAT_IF_SOLVER_TIMEOUT_MS

# Prepared paths kept per API worker process, so that the reports several
# readers are checking at once stay prepared in every worker. A path keeps
# each value of its trace once, with the steps it holds for.
WHAT_IF_CACHE_SIZE = 8

NO_TRACE_REASON = \
    "This report has no Constraint Snapshots, so this path cannot be checked."
NO_SMTLIB_REASON = \
    "This report has no solver input for this path. Recording it needs a " \
    "Clang built with Z3."
OLD_TRACE_REASON = \
    "This report's Constraint Snapshots were recorded in an older format, " \
    "so this path cannot be checked. Analyze and store it again to check it."
UNREADABLE_TRACE_REASON = \
    "This report's Constraint Snapshots are unreadable, so this path cannot " \
    "be checked."


class ConstraintSyntaxError(Exception):
    """ The reader's text is not a What-If Constraint. """

    def __init__(self, message: str, offset: int):
        super().__init__(message)
        self.offset = offset


class _Unanswerable(Exception):
    """ A constraint that can be read but not checked on this path. """


@dataclass(frozen=True)
class Term:
    """ `variable op value`, where a `null` value is 0. """
    variable: str
    op: str
    value: int


@dataclass(frozen=True)
class _Applied:
    """ A term on one value of its variable, from Path Step `since` on. """
    formula: object
    since: int


@dataclass(frozen=True)
class _Token:
    kind: str
    text: str
    offset: int


_TOKEN = re.compile(
    r'\s*(?:(?P<number>-?\d+)|(?P<name>[A-Za-z_]\w*)|(?P<and>&&)'
    r'|(?P<op>==|!=|<=|>=|<|>)|(?P<other>\S))')

OPERATORS = ('==', '!=', '<', '<=', '>', '>=')

_COMPARE = {'==': lambda a, b: a == b, '!=': lambda a, b: a != b,
            '<': lambda a, b: a < b, '<=': lambda a, b: a <= b,
            '>': lambda a, b: a > b, '>=': lambda a, b: a >= b}


class _Tokens:
    """ The reader's text as tokens, consumed from the front. """

    def __init__(self, text: str):
        self._tokens = [_Token(match.lastgroup, match.group(match.lastgroup),
                               match.start(match.lastgroup))
                        for match in _TOKEN.finditer(text)
                        if match.lastgroup]
        self._end = len(text)

    def __bool__(self):
        return bool(self._tokens)

    def pop(self, kinds: tuple[str, ...], what: str) -> _Token:
        if not self._tokens:
            raise ConstraintSyntaxError(f"Expected {what} at the end.",
                                        self._end)
        token = self._tokens[0]
        if token.kind not in kinds:
            raise ConstraintSyntaxError(
                f"Expected {what}, found `{token.text}`.", token.offset)
        return self._tokens.pop(0)


def _parse_term(tokens: _Tokens) -> Term:
    variable = tokens.pop(('name',), 'a variable name').text
    op = tokens.pop(('op',), 'an operator (' +
                    ', '.join(f'`{op}`' for op in OPERATORS) + ')').text
    value = tokens.pop(('number', 'name'), 'an integer or null')
    if value.kind == 'name' and value.text != 'null':
        raise ConstraintSyntaxError(
            f"Expected an integer or null, found `{value.text}`.",
            value.offset)
    return Term(variable, op, 0 if value.text == 'null' else int(value.text))


def parse_constraint(text: str) -> list[Term]:
    """
    Parse the reader's text into the terms that must all hold. Raises
    ConstraintSyntaxError with a message for the reader and where reading
    failed.
    """
    tokens = _Tokens(text)
    if not tokens:
        raise ConstraintSyntaxError("Expected `variable op value`.", 0)
    terms = [_parse_term(tokens)]
    while tokens:
        tokens.pop(('and',), '`&&` or the end')
        terms.append(_parse_term(tokens))
    return terms


def _concrete_term(term: Term, variable: dict, ctx: z3.Context):
    """
    The term on a concretely known value, decided by comparing the two. An
    address is never null, but its number is not recorded.
    """
    if variable['kind'] == 'integer':
        holds = _COMPARE[term.op](int(variable['value']), term.value)
    elif term.value == 0:
        holds = term.op in ('!=', '>', '>=')
    else:
        raise _Unanswerable(
            f"`{term.variable}` holds {variable['value']}, whose numeric "
            "value is not recorded, so only a comparison with 0 or null can "
            "be decided.")
    return z3.BoolVal(holds, ctx=ctx)


def _bitvector_term(term: Term, symbol, signed: bool, ctx: z3.Context):
    """ The term as a Z3 formula over the variable's SMT symbol. """
    width = symbol.size()

    low, high = (-2 ** (width - 1), 2 ** (width - 1) - 1) if signed \
        else (0, 2 ** width - 1)
    if not low <= term.value <= high:
        raise _Unanswerable(
            f"{term.value} is outside the range of `{term.variable}` "
            f"({low} to {high}).")

    value = z3.BitVecVal(term.value, width, ctx=ctx)
    if term.op == '==':
        return symbol == value
    if term.op == '!=':
        return symbol != value
    if signed:
        return {'<': symbol < value, '<=': symbol <= value,
                '>': symbol > value, '>=': symbol >= value}[term.op]
    return {'<': z3.ULT(symbol, value), '<=': z3.ULE(symbol, value),
            '>': z3.UGT(symbol, value), '>=': z3.UGE(symbol, value)}[term.op]


def _solve(ctx: z3.Context, assertions, extra,
           timeout_ms: int = WHAT_IF_SOLVER_TIMEOUT_MS
           ) -> tuple[int, str | None]:
    solver = z3.Solver(ctx=ctx)
    solver.set('timeout', timeout_ms)
    solver.add(assertions)
    solver.add(*extra)

    result = solver.check()
    if result == z3.sat:
        return PathVerdict.FEASIBLE, None
    if result == z3.unsat:
        return PathVerdict.INFEASIBLE, None

    why = solver.reason_unknown()
    if why in ('timeout', 'canceled'):
        return PathVerdict.UNKNOWN, \
            f"The solver gave up on this path after " \
            f"{WHAT_IF_SOLVER_TIMEOUT_MS} ms."
    return PathVerdict.UNKNOWN, f"The solver has no answer for this path " \
        f"({why})."


class PreparedPath:
    """
    A Bug Path read from its stored constraint trace for checking: its Path
    Steps, their solver input parsed as it is needed, and its Baseline
    Verdict once solved. None of it depends on What-If Constraints, so checks
    of one report can share it.
    """

    def __init__(self, trace_json: str | None):
        self.path = None
        self.unchecked_reason = None
        self._baseline = None

        trace = json.loads(trace_json) if trace_json else None
        if not trace:
            self.unchecked_reason = NO_TRACE_REASON
            return
        if trace.get('version') != TRACE_VERSION:
            self.unchecked_reason = OLD_TRACE_REASON
            return
        if not trace.get('steps'):
            self.unchecked_reason = NO_TRACE_REASON
            return
        try:
            constraint_trace = ConstraintTrace(trace)
            constraint_trace.check()
            self.path = _Path(constraint_trace)
        except (MalformedTrace, KeyError, IndexError, TypeError) as ex:
            LOG.error("Unreadable constraint trace: %s", ex)
            self.unchecked_reason = UNREADABLE_TRACE_REASON
            return
        if not self.path.smtlib:
            self.unchecked_reason = NO_SMTLIB_REASON

    def baseline(self) -> tuple[int, str | None]:
        """
        The verdict on the Bug Path alone. One the solver gave up on is not
        kept, because a later try may get an answer.
        """
        if self._baseline is not None:
            return self._baseline
        baseline = _solve(self.path.ctx,
                          self.path.assertions(len(self.path.steps) - 1), [])
        if baseline[0] != PathVerdict.UNKNOWN:
            self._baseline = baseline
        return baseline


_prepared_paths: OrderedDict = OrderedDict()


def prepared_path(key, load_trace) -> PreparedPath:
    """
    The PreparedPath cached under `key`, which must change whenever the
    stored trace does, or one read from `load_trace()` on a miss. The least
    recently used of more than WHAT_IF_CACHE_SIZE paths is dropped.
    """
    path = _prepared_paths.pop(key, None)
    if path is None:
        path = PreparedPath(load_trace())
    _prepared_paths[key] = path
    while len(_prepared_paths) > WHAT_IF_CACHE_SIZE:
        _prepared_paths.popitem(last=False)
    return path


def check_what_if(path: PreparedPath, constraints) -> WhatIfResult:
    """
    Check a Bug Path against What-If Constraints, a list of objects with
    `pathStep`, `expression` and `holdsThroughout`, which must all hold
    together.

    A constraint that cannot be read is reported as a syntax error, with the
    Baseline Verdict, and nothing is solved with the constraints.
    """
    parsed = []
    for index, constraint in enumerate(constraints):
        try:
            parsed.append((constraint.pathStep,
                           parse_constraint(constraint.expression),
                           bool(constraint.holdsThroughout)))
        except ConstraintSyntaxError as ex:
            result = _check_path(path, [])
            result.verdict = PathVerdict.UNKNOWN
            result.reason = f"Cannot read the constraint: {ex}"
            result.syntaxError = WhatIfSyntaxError(index, str(ex), ex.offset)
            return result

    return _check_path(path, parsed)


def _check_path(prepared: PreparedPath,
                constraints: list[tuple[int, list[Term], bool]]
                ) -> WhatIfResult:
    """
    The path condition accumulated at the last Path Step holds every
    constraint of the path, so it is what both verdicts are solved over. A
    constraint's anchor only decides which value its variables stand for,
    unless it holds throughout, when every value they take counts.
    """
    if prepared.unchecked_reason:
        return WhatIfResult(PathVerdict.UNKNOWN, prepared.unchecked_reason,
                            PathVerdict.UNKNOWN, prepared.unchecked_reason)

    path = prepared.path
    ctx = path.ctx
    try:
        baseline, baseline_reason = prepared.baseline()
    except z3.Z3Exception as ex:
        LOG.error("Unreadable SMT-LIB in a constraint trace: %s", ex)
        reason = "The solver input recorded for this path is unreadable."
        return WhatIfResult(PathVerdict.UNKNOWN, reason,
                            PathVerdict.UNKNOWN, reason)

    applied = []
    try:
        for index, (order, terms, throughout) in enumerate(constraints):
            try:
                if order not in path.by_order:
                    raise _Unanswerable(f"This path has no Path Step {order}.")
                for term in terms:
                    applied.extend(path.throughout(term) if throughout
                                   else path.anchored(term, order))
            except _Unanswerable as ex:
                return WhatIfResult(PathVerdict.UNKNOWN, str(ex), baseline,
                                    baseline_reason,
                                    reasonConstraintIndex=index)

        result = WhatIfResult(
            *_solve(ctx, path.assertions(len(path.steps) - 1),
                    [a.formula for a in applied]),
            baseline, baseline_reason)
        if result.verdict == PathVerdict.INFEASIBLE:
            result.firstDeadStep, result.firstDeadStepIsEarliest = \
                _first_dead_step(path, applied)
        return result
    except z3.Z3Exception as ex:
        LOG.error("Unreadable SMT-LIB in a constraint trace: %s", ex)
        return WhatIfResult(
            PathVerdict.UNKNOWN,
            "The solver input recorded for this path is unreadable.",
            baseline, baseline_reason)


def _first_dead_step(path: '_Path',
                     applied: list[_Applied]) -> tuple[int, bool]:
    """
    The `order` of the earliest Path Step whose accumulated path condition,
    with the terms applied by then, is unsatisfiable, and whether the search
    proved it earliest. The last step is known to be dead.

    Ranges only narrow along a path and applied terms are never taken back,
    so once the path is dead it stays dead, and a binary search finds the
    step. A step the solver gives up on counts as alive, so the named step is
    always one proven dead; when the search budget runs out it is the
    earliest step proven dead so far. Either way it is not proven earliest.
    """
    alive, dead = -1, len(path.steps) - 1
    gave_up = False
    deadline = time.monotonic() + WHAT_IF_SEARCH_BUDGET_MS / 1000
    while dead - alive > 1:
        remaining_ms = int((deadline - time.monotonic()) * 1000)
        if remaining_ms <= 0:
            break
        middle = (alive + dead) // 2
        step = path.steps[middle]
        verdict, _ = _solve(
            path.ctx, path.assertions(middle),
            [a.formula for a in applied if a.since <= step['order']],
            min(WHAT_IF_SOLVER_TIMEOUT_MS, remaining_ms))
        if verdict == PathVerdict.INFEASIBLE:
            dead = middle
        else:
            gave_up = gave_up or verdict == PathVerdict.UNKNOWN
            alive = middle
    return path.steps[dead]['order'], dead - alive == 1 and not gave_up


_DECLARED = re.compile(r'\(declare-fun\s+(\S+)')
_SMT_SYMBOL = re.compile(r'[^\s()]+')


class _Path:
    """
    The Path Steps of a Bug Path in order, the values they hold, and their
    solver input in a Z3 context of its own.

    A step shows the snapshot of its analyzer node, and each value of the
    trace is kept once, with the span of nodes it holds for, so a long path
    costs what its trace does rather than its steps times their snapshots.
    """

    def __init__(self, trace: ConstraintTrace):
        self.steps = sorted(({'order': step['order'],
                              'node': step['exploded_node']}
                             for step in trace.steps),
                            key=lambda step: step['order'])
        self.smtlib = trace.smtlib
        self.ctx = z3.Context()
        self._assertions = {}
        self._symbols = {}
        self.by_order = {step['order']: index
                         for index, step in enumerate(self.steps)}

        self._variables: dict[str, list] = {}
        for life in trace.lifetimes('variables'):
            variable = trace.entry('variables', life.entry)
            self._variables.setdefault(variable['name'], []).append(
                (variable, life.start, life.end))
        self._path_condition = [
            (trace.assertion(life.entry), life.start, life.end)
            for life in trace.lifetimes('path_condition')] \
            if self.smtlib else []
        # Symbol IDs are unique within a report, so declaring every symbol of
        # the path lets any step's assertions be read with any term.
        declarations = sorted(trace.lifetimes('smt_declarations'),
                              key=lambda life: life.start)
        self.declarations = list(dict.fromkeys(
            trace.entry('smt_declarations', life.entry)
            for life in declarations))
        self._declared: dict[str, list[tuple[int, int]]] = {}
        for life in declarations:
            match = _DECLARED.match(
                trace.entry('smt_declarations', life.entry))
            if match:
                self._declared.setdefault(match.group(1), []).append(
                    (life.start, life.end))

    def assertions(self, index: int):
        """ The path condition accumulated at step `index`, parsed. """
        node = self.steps[index]['node']
        if node not in self._assertions:
            self._assertions[node] = z3.parse_smt2_string(
                "\n".join(self.declarations +
                          [line for line, start, end in self._path_condition
                           if start <= node < end]),
                ctx=self.ctx)
        return self._assertions[node]

    def symbol(self, smt: str):
        """ The Z3 term of a variable's SMT symbol. """
        if smt not in self._symbols:
            # parse_smt2_string only returns assertions, so the symbol's term
            # is read back from a trivial one.
            self._symbols[smt] = z3.parse_smt2_string(
                "\n".join(self.declarations) + f"\n(assert (= {smt} {smt}))",
                ctx=self.ctx)[0].arg(0)
        return self._symbols[smt]

    def anchored(self, term: Term, order: int) -> list[_Applied]:
        """ The term on the variable's value at the anchor step. """
        index = self.by_order[order]
        variable = self._variable(index, term.variable)
        if variable is None:
            raise _Unanswerable(
                f"`{term.variable}` has no tracked value at this Path Step.")
        return [_Applied(self._formula(term, variable),
                         self.steps[self._since(index, variable)]['order'])]

    def throughout(self, term: Term) -> list[_Applied]:
        """ The term on every value the variable takes along the path. """
        applied = {}
        for index in range(len(self.steps)):
            variable = self._variable(index, term.variable)
            if variable is None:
                continue
            key = (variable['kind'], variable['value'])
            if key not in applied:
                applied[key] = _Applied(
                    self._formula(term, variable),
                    self.steps[self._since(index, variable)]['order'])
        if not applied:
            raise _Unanswerable(
                f"`{term.variable}` has no tracked value at any Path Step.")
        return list(applied.values())

    def _variable(self, index: int, name: str) -> dict | None:
        node = self.steps[index]['node']
        return next((variable for variable, start, end
                     in self._variables.get(name, ())
                     if start <= node < end), None)

    def _is_declared(self, name: str, index: int) -> bool:
        node = self.steps[index]['node']
        return any(start <= node < end
                   for start, end in self._declared.get(name, ()))

    def _since(self, index: int, variable: dict) -> int:
        """
        Where the value the variable holds at step `index` appears: a symbol
        at the first step that declares all of its term's symbols, since a
        symbol never changes value; a concrete value at the start of the run
        of steps up to `index` where the variable holds it.
        """
        if variable['kind'] == 'symbol' and 'smt' in variable:
            names = {name for name in _SMT_SYMBOL.findall(variable['smt'])
                     if self._is_declared(name, index)}
            return next(earlier for earlier in range(index + 1)
                        if all(self._is_declared(name, earlier)
                               for name in names))

        while index > 0 and self._variable(index - 1, variable['name']) \
                == variable:
            index -= 1
        return index

    def _formula(self, term: Term, variable: dict):
        if variable['kind'] != 'symbol':
            return _concrete_term(term, variable, self.ctx)
        if 'smt' not in variable:
            raise _Unanswerable(
                f"`{term.variable}` holds {variable['value']}, whose type "
                "the check cannot reason about.")
        return _bitvector_term(term, self.symbol(variable['smt']),
                               variable['smt_signed'], self.ctx)
