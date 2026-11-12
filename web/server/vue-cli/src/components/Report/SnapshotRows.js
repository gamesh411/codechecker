// The rows of the Constraint Snapshot panel, from a Path Step's snapshot.
//
// A row is told apart from the others by its key, which is the symbol's dump
// or the variable's name, never its label: two symbols can read the same, such
// as two results of one call, and must stay two rows.

import { compareNames } from "./ConstraintTrace";

// What this step did to a symbol's entry in a list, or undefined.
function changeIn(step, field, symbol) {
  return step.changes?.[field].get(symbol);
}

// { variables, others, accumulated } for the step. `variables` are the
// variables, by name, `others` the symbols with a range that no variable
// holds, and `accumulated` the path condition, named rows first.
export function snapshotRows(step) {
  const _known = new Map(
    step.symbolRanges.map(range => [ range.symbol, range.range ]));

  const variables = [ ...step.variables ]
    .sort((a, b) => compareNames(a.name, b.name))
    .map(variable => {
      const _isSymbol = variable.kind === "symbol";
      return {
        key: `variable ${variable.name}`,
        name: variable.name,
        range: _isSymbol ? _known.get(variable.value) : undefined,
        display: _isSymbol ? "no known range" : variable.value,
        symbol: _isSymbol ? variable.value : null,
        muted: _isSymbol && !_known.has(variable.value),
        change: _isSymbol
          ? changeIn(step, "symbol_ranges", variable.value) : undefined
      };
    });

  const _named = new Set(step.variables.map(variable => variable.value));
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

  return { variables, others, accumulated };
}
