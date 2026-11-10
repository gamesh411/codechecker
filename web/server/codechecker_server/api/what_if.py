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
    WhatIfResult

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


class WhatIfSyntaxError(Exception):
    """ The reader's text is not a What-If Constraint. """


class _Unanswerable(Exception):
    """ A constraint that can be read but not checked on this path. """


@dataclass(frozen=True)
class Term:
    """ `variable op value`. """
    variable: str
    op: str
    value: int


_TOKEN = re.compile(
    r'\s*(?:(?P<number>-?\d+)|(?P<name>[A-Za-z_]\w*)'
    r'|(?P<op>==|!=|<=|>=|<|>)|(?P<other>\S))')

OPERATORS = ('==', '!=', '<', '<=', '>', '>=')


def _tokenize(text: str) -> list[tuple[str, str]]:
    tokens = []
    for match in _TOKEN.finditer(text):
        if match.lastgroup:
            tokens.append((match.lastgroup, match.group(match.lastgroup)))
    return tokens


def _expect(tokens: list[tuple[str, str]], kind: str, what: str) -> str:
    if not tokens:
        raise WhatIfSyntaxError(f"Expected {what} at the end.")
    token_kind, text = tokens.pop(0)
    if token_kind != kind:
        raise WhatIfSyntaxError(f"Expected {what}, found `{text}`.")
    return text


def _parse_term(tokens: list[tuple[str, str]]) -> Term:
    variable = _expect(tokens, 'name', 'a variable name')
    op = _expect(tokens, 'op', 'one of ' + ' '.join(OPERATORS))
    value = int(_expect(tokens, 'number', 'an integer'))
    return Term(variable, op, value)


def parse_constraint(text: str) -> list[Term]:
    """
    Parse the reader's text into the terms that must all hold. Raises
    WhatIfSyntaxError with a message for the reader.
    """
    tokens = _tokenize(text)
    if not tokens:
        raise WhatIfSyntaxError("Expected `variable op value`.")
    terms = [_parse_term(tokens)]
    if tokens:
        raise WhatIfSyntaxError(f"Unexpected `{tokens[0][1]}`.")
    return terms


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
    with `pathStep` and `expression`.

    The path condition accumulated at the last Path Step holds every
    constraint of the path, so it is what both verdicts are solved over. A
    constraint's anchor only decides which symbol its variable stands for.
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

    def result(verdict, reason=None):
        return WhatIfResult(verdict, reason, baseline, baseline_reason)

    declarations = last['smt_declarations']
    formulas = []
    try:
        for constraint in constraints:
            step = steps.get(constraint.pathStep)
            if step is None:
                raise _Unanswerable(
                    f"This path has no Path Step {constraint.pathStep}.")
            declarations = _union(declarations,
                                  step.get('smt_declarations', []))
            variables = {variable['name']: variable
                         for variable in step['variables']}

            for term in parse_constraint(constraint.expression):
                variable = variables.get(term.variable)
                if variable is None:
                    raise _Unanswerable(
                        f"`{term.variable}` has no tracked value at this "
                        "Path Step.")
                if 'smt' not in variable:
                    raise _Unanswerable(
                        f"`{term.variable}` holds {variable['value']} at "
                        "this Path Step, and checking a concrete value is "
                        "not supported yet.")
                formulas.append(_bitvector_term(
                    term, variable['smt'], variable['smt_signed'],
                    declarations, ctx))

        return result(*_solve(ctx, declarations, last['smt_assertions'],
                              formulas))
    except WhatIfSyntaxError as ex:
        return result(PathVerdict.UNKNOWN, f"Cannot read the constraint: {ex}")
    except _Unanswerable as ex:
        return result(PathVerdict.UNKNOWN, str(ex))
    except z3.Z3Exception as ex:
        LOG.error("Unreadable SMT-LIB in a constraint trace: %s", ex)
        return result(PathVerdict.UNKNOWN,
                      "The solver input recorded for this path is unreadable.")
