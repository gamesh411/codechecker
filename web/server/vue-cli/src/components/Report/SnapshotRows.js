// The rows of the Constraint Snapshot panel, from a Path Step's snapshot.
//
// A row is told apart from the others by its key, which is the symbol's dump
// or the variable's name, never its label: two symbols can read the same, such
// as two results of one call, and must stay two rows.

import { compareNames } from "./ConstraintTrace";

// Callers' groups start collapsed when there are more of them than this, so
// that a deep call stack does not bury the current function.
export const EXPANDED_CALLERS = 3;

// What this step did to a symbol's entry in a list, or undefined.
function changeIn(step, field, symbol) {
  return step.changes?.[field].get(symbol);
}

// The rows of one frame's variables, by name. `prefix` keeps the keys of two
// frames' `n` apart. A symbol the analyzer dropped at this step shows the
// last range the path condition has for it, marked `dropped`, as its own row
// in the path condition is.
function variableRows(step, variables, known, accumulated, prefix) {
  return [ ...variables ]
    .sort((a, b) => compareNames(a.name, b.name))
    .map(variable => {
      const _isSymbol = variable.kind === "symbol";
      const _known = _isSymbol && known.has(variable.value);
      const _dropped = _isSymbol && !_known && accumulated.has(variable.value);
      return {
        key: `${prefix}${variable.name}`,
        name: variable.name,
        range: _isSymbol
          ? known.get(variable.value) ?? accumulated.get(variable.value)
          : undefined,
        display: _isSymbol ? "no known range" : variable.value,
        symbol: _isSymbol ? variable.value : null,
        dropped: _dropped,
        muted: _isSymbol && !_known && !_dropped,
        change: _isSymbol
          ? changeIn(step, "symbol_ranges", variable.value) : undefined
      };
    });
}

// The heading of a frame's group: { key, frame, function, callSite, parent },
// from the trace's stack frame at `frame`.
function frameGroup(step, frame) {
  const _frame = step.frames?.[frame];
  return {
    key: `frame ${frame}`,
    frame,
    function: _frame?.function ?? "function",
    callSite: _frame?.call_site ?? null,
    parent: _frame?.parent
  };
}

// { variables, current, callers, others, accumulated } for the step.
// `variables` are the variables of the step's own frame, by name, and
// `current` that frame's heading. `callers` has one group per calling frame
// that has variables, innermost first, as a heading with `rows`, from the
// step's `callerVariables`, `frame` (its own frame) and `frames` (the trace's
// stack frames). `others` are the symbols with a range that no variable of
// any frame holds, and `accumulated` the path condition, named rows first.
export function snapshotRows(step) {
  const _known = new Map(
    step.symbolRanges.map(range => [ range.symbol, range.range ]));
  const _accumulated = new Map(
    step.pathCondition.map(range => [ range.symbol, range.range ]));

  const variables = variableRows(step, step.variables, _known, _accumulated,
    "variable ");

  const _byFrame = new Map();
  (step.callerVariables ?? []).forEach(variable => {
    if (!_byFrame.has(variable.frame)) _byFrame.set(variable.frame, []);
    _byFrame.get(variable.frame).push(variable);
  });
  // The trace lists frames innermost first, and a Map keeps that order.
  const callers = [ ..._byFrame ].map(([ frame, frameVariables ]) => ({
    ...frameGroup(step, frame),
    rows: variableRows(step, frameVariables, _known, _accumulated,
      `frame ${frame} variable `)
  }));
  const current = step.frame === undefined
    ? null : frameGroup(step, step.frame);

  const _named = new Set([ ...step.variables, ...step.callerVariables ?? [] ]
    .map(variable => variable.value));
  const others = step.symbolRanges
    .filter(range => !_named.has(range.symbol))
    .sort((a, b) => compareNames(a.symbol, b.symbol))
    .map(range => ({
      key: `symbol ${range.symbol}`,
      symbol: range.symbol,
      range: range.range,
      change: changeIn(step, "symbol_ranges", range.symbol)
    }));

  const accumulated = step.pathCondition.map(range => ({
    key: `symbol ${range.symbol}`,
    symbol: range.symbol,
    range: range.range,
    name: step.symbolNames.get(range.symbol),
    dropped: !_known.has(range.symbol),
    change: changeIn(step, "path_condition", range.symbol)
  })).sort((a, b) =>
    (a.name === undefined) - (b.name === undefined) ||
    compareNames(a.name ?? a.symbol, b.name ?? b.symbol));

  return { variables, current, callers, others, accumulated };
}
