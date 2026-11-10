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
from dataclasses import dataclass

import z3

from codechecker_api.python.DBAccess_v6.ttypes import PathVerdict, \
    WhatIfResult, WhatIfSyntaxError

from codechecker_common.logger import get_logger

LOG = get_logger('server')

# Per solver call. A check makes two calls, the Baseline Verdict and the one
# with the reader's constraints, so an API worker is held at most twice this.
WHAT_IF_SOLVER_TIMEOUT_MS = 2000

NO_TRACE_REASON = \
    "This report has no Constraint Snapshots, so this path cannot be checked."
NO_SMTLIB_REASON = \
    "This report has no solver input for this path. Recording it needs a " \
    "Clang built with Z3."


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


def _bitvector_term(term: Term, smt: str, signed: bool,
                    declarations: list[str], ctx: z3.Context):
    """ The term as a Z3 formula over the variable's SMT symbol. """
    # parse_smt2_string only returns assertions, so the symbol's term is read
    # back from a trivial one.
    symbol = z3.parse_smt2_string(
        "\n".join(declarations) + f"\n(assert (= {smt} {smt}))",
        ctx=ctx)[0].arg(0)
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


def _solve(ctx: z3.Context, declarations: list[str], assertions: list[str],
           extra) -> tuple[int, str | None]:
    solver = z3.Solver(ctx=ctx)
    solver.set('timeout', WHAT_IF_SOLVER_TIMEOUT_MS)
    solver.add(z3.parse_smt2_string(
        "\n".join(declarations + assertions), ctx=ctx))
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


def _union(*lists: list[str]) -> list[str]:
    return list(dict.fromkeys(line for lines in lists for line in lines))


def check_what_if(trace_json: str | None, constraints) -> WhatIfResult:
    """
    Check the Bug Path whose Constraint Snapshots are in `trace_json` (the
    stored constraint trace) against What-If Constraints, a list of objects
    with `pathStep` and `expression`, which must all hold together.

    A constraint that cannot be read is reported as a syntax error, with the
    Baseline Verdict, and nothing is solved with the constraints.
    """
    parsed = []
    for index, constraint in enumerate(constraints):
        try:
            parsed.append((constraint.pathStep,
                           parse_constraint(constraint.expression)))
        except ConstraintSyntaxError as ex:
            result = _check_path(trace_json, [])
            result.verdict = PathVerdict.UNKNOWN
            result.reason = f"Cannot read the constraint: {ex}"
            result.syntaxError = WhatIfSyntaxError(index, str(ex), ex.offset)
            return result

    return _check_path(trace_json, parsed)


def _check_path(trace_json: str | None,
                constraints: list[tuple[int, list[Term]]]) -> WhatIfResult:
    """
    The path condition accumulated at the last Path Step holds every
    constraint of the path, so it is what both verdicts are solved over. A
    constraint's anchor only decides which value its variables stand for.
    """
    trace = json.loads(trace_json) if trace_json else None
    if not trace or not trace.get('steps'):
        return WhatIfResult(PathVerdict.UNKNOWN, NO_TRACE_REASON,
                            PathVerdict.UNKNOWN, NO_TRACE_REASON)

    steps = {step['order']: step for step in trace['steps']}
    last = steps[max(steps)]
    if 'smt_assertions' not in last:
        return WhatIfResult(PathVerdict.UNKNOWN, NO_SMTLIB_REASON,
                            PathVerdict.UNKNOWN, NO_SMTLIB_REASON)

    ctx = z3.Context()
    try:
        baseline, baseline_reason = _solve(
            ctx, last['smt_declarations'], last['smt_assertions'], [])
    except z3.Z3Exception as ex:
        LOG.error("Unreadable SMT-LIB in a constraint trace: %s", ex)
        reason = "The solver input recorded for this path is unreadable."
        return WhatIfResult(PathVerdict.UNKNOWN, reason,
                            PathVerdict.UNKNOWN, reason)

    declarations = last['smt_declarations']
    formulas = []
    try:
        for index, (order, terms) in enumerate(constraints):
            try:
                step = steps.get(order)
                if step is None:
                    raise _Unanswerable(f"This path has no Path Step {order}.")
                declarations = _union(declarations,
                                      step.get('smt_declarations', []))
                formulas.extend(_step_terms(terms, step, declarations, ctx))
            except _Unanswerable as ex:
                return WhatIfResult(PathVerdict.UNKNOWN, str(ex), baseline,
                                    baseline_reason,
                                    reasonConstraintIndex=index)

        return WhatIfResult(*_solve(ctx, declarations, last['smt_assertions'],
                                    formulas),
                            baseline, baseline_reason)
    except z3.Z3Exception as ex:
        LOG.error("Unreadable SMT-LIB in a constraint trace: %s", ex)
        return WhatIfResult(
            PathVerdict.UNKNOWN,
            "The solver input recorded for this path is unreadable.",
            baseline, baseline_reason)


def _step_terms(terms: list[Term], step: dict, declarations: list[str],
                ctx: z3.Context) -> list:
    """ Z3 formulas for terms on the variables of one Path Step. """
    variables = {variable['name']: variable for variable in step['variables']}
    formulas = []
    for term in terms:
        variable = variables.get(term.variable)
        if variable is None:
            raise _Unanswerable(
                f"`{term.variable}` has no tracked value at this Path Step.")
        if variable['kind'] != 'symbol':
            formulas.append(_concrete_term(term, variable, ctx))
        elif 'smt' not in variable:
            raise _Unanswerable(
                f"`{term.variable}` holds {variable['value']}, whose type "
                "the check cannot reason about.")
        else:
            formulas.append(_bitvector_term(
                term, variable['smt'], variable['smt_signed'],
                declarations, ctx))
    return formulas
