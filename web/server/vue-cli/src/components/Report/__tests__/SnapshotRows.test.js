import { snapshotRows } from "@/components/Report/SnapshotRows";

// Two results of one call: the analyzer made two symbols, and a label cannot
// tell them apart.
const FIRST = "conj_$2{int, LC1, S90, #1}";
const SECOND = "conj_$7{int, LC1, S95, #1}";

function stepWith(fields) {
  return {
    symbolRanges: [], variables: [], pathCondition: [],
    symbolNames: new Map(), labelOf: () => ({ text: "result of size()" }),
    ...fields
  };
}

describe("rows of the snapshot panel", () => {
  test("two symbols with one label stay two rows of the path condition", () => {
    const step = stepWith({
      pathCondition: [
        { symbol: FIRST, range: "{ [0, 0] }" },
        { symbol: SECOND, range: "{ [1, 1] }" }
      ]
    });
    expect(step.labelOf(FIRST).text).toBe(step.labelOf(SECOND).text);

    const { accumulated } = snapshotRows(step);
    expect(accumulated.map(row => row.symbol)).toEqual([ FIRST, SECOND ]);
    expect(new Set(accumulated.map(row => row.key)).size).toBe(2);
    expect(accumulated.map(row => row.range))
      .toEqual([ "{ [0, 0] }", "{ [1, 1] }" ]);
  });

  test("and two other symbols with one label stay two rows", () => {
    const { others } = snapshotRows(stepWith({
      symbolRanges: [
        { symbol: SECOND, range: "{ [1, 1] }" },
        { symbol: FIRST, range: "{ [0, 0] }" }
      ]
    }));
    expect(others.map(row => row.symbol)).toEqual([ FIRST, SECOND ]);
    expect(new Set(others.map(row => row.key)).size).toBe(2);
  });

  test("two variables holding one symbol each keep a row", () => {
    const { variables } = snapshotRows(stepWith({
      variables: [
        { name: "b", value: FIRST, kind: "symbol" },
        { name: "a", value: FIRST, kind: "symbol" }
      ],
      symbolRanges: [ { symbol: FIRST, range: "{ [0, 0] }" } ]
    }));
    expect(variables.map(row => row.name)).toEqual([ "a", "b" ]);
    expect(new Set(variables.map(row => row.key)).size).toBe(2);
    expect(variables[0].range).toBe("{ [0, 0] }");
  });

  test("a variable and a symbol of the same text do not share a key", () => {
    const step = stepWith({
      variables: [ { name: "x", value: "5", kind: "integer" } ],
      symbolRanges: [ { symbol: "x", range: "{ [1, 1] }" } ],
      pathCondition: [ { symbol: "x", range: "{ [1, 1] }" } ]
    });
    const rows = snapshotRows(step);
    expect(rows.variables[0].key).not.toBe(rows.others[0].key);
    expect(rows.variables[0]).toMatchObject({
      symbol: null, display: "5", muted: false
    });
  });

  test("named rows of the path condition come first", () => {
    const { accumulated } = snapshotRows(stepWith({
      symbolRanges: [ { symbol: SECOND, range: "{ [1, 1] }" } ],
      symbolNames: new Map([ [ SECOND, "n" ] ]),
      pathCondition: [
        { symbol: FIRST, range: "{ [0, 0] }" },
        { symbol: SECOND, range: "{ [1, 1] }" }
      ]
    }));
    expect(accumulated.map(row => row.symbol)).toEqual([ SECOND, FIRST ]);
    expect(accumulated.map(row => row.dropped)).toEqual([ false, true ]);
  });

  test("a step's changes reach its rows", () => {
    const changes = {
      symbol_ranges: new Map([ [ FIRST, "narrowed" ] ]),
      path_condition: new Map([ [ SECOND, "added" ] ])
    };
    const rows = snapshotRows(stepWith({
      changes,
      symbolRanges: [ { symbol: FIRST, range: "{ [0, 0] }" } ],
      pathCondition: [ { symbol: SECOND, range: "{ [1, 1] }" } ]
    }));
    expect(rows.others[0].change).toBe("narrowed");
    expect(rows.accumulated[0].change).toBe("added");
  });

  test("each calling frame is a group of its own, innermost first", () => {
    const rows = snapshotRows(stepWith({
      frame: 2,
      frames: [
        { function: "top" },
        { function: "middle", parent: 0, call_site: { line: 19 } },
        { function: "inner", parent: 1, call_site: { line: 13 } }
      ],
      variables: [ { name: "n", value: "7", kind: "integer" } ],
      callerVariables: [
        { frame: 1, name: "n", value: "6", kind: "integer" },
        { frame: 1, name: "m", value: FIRST, kind: "symbol" },
        { frame: 0, name: "n", value: SECOND, kind: "symbol" }
      ],
      symbolRanges: [
        { symbol: FIRST, range: "{ [6, 9] }" },
        { symbol: SECOND, range: "{ [2, 2] }" }
      ],
      changes: {
        symbol_ranges: new Map([ [ SECOND, "added" ] ]),
        path_condition: new Map()
      }
    }));
    expect(rows.current).toMatchObject(
      { function: "inner", callSite: { line: 13 }, parent: 1 });
    expect(rows.callers.map(group => group.function))
      .toEqual([ "middle", "top" ]);
    expect(rows.callers[0].callSite).toEqual({ line: 19 });
    expect(rows.callers[1].callSite).toBeNull();
    expect(rows.callers[0].rows.map(row => row.name)).toEqual([ "m", "n" ]);
    expect(rows.callers[0].rows[0].range).toBe("{ [6, 9] }");
    expect(rows.callers[1].rows[0]).toMatchObject(
      { name: "n", symbol: SECOND, change: "added" });
    // Three `n` rows, three keys.
    const _keys = [ rows.variables, ...rows.callers.map(group => group.rows) ]
      .flat().filter(row => row.name === "n").map(row => row.key);
    expect(new Set(_keys).size).toBe(3);
    // What a caller's variable holds is not an other symbol.
    expect(rows.others).toEqual([]);
  });

  test("a step without callers has none", () => {
    const rows = snapshotRows(stepWith({
      variables: [ { name: "x", value: "5", kind: "integer" } ]
    }));
    expect(rows.callers).toEqual([]);
    expect(rows.current).toBeNull();
    expect(rows.variables.map(row => row.key)).toEqual([ "variable x" ]);
  });

  test("a variable whose range was dropped shows the last one, marked", () => {
    const TOP_N = "reg_$1<int n>";
    const { variables, callers, accumulated } = snapshotRows(stepWith({
      variables: [ { name: "m", value: FIRST, kind: "symbol" } ],
      callerVariables: [
        { frame: 0, name: "n", value: TOP_N, kind: "symbol" }
      ],
      frame: 1,
      frames: [ { function: "top" }, { function: "inner", parent: 0 } ],
      pathCondition: [ { symbol: TOP_N, range: "{ [2, 2] }" } ]
    }));
    const [ n ] = callers[0].rows;
    expect([ n.range, n.dropped, n.muted ]).toEqual([ "{ [2, 2] }", true,
      false ]);
    expect(accumulated[0].dropped).toBe(true);
    // Neither known nor ever constrained: still no known range.
    expect([ variables[0].range, variables[0].dropped, variables[0].muted,
      variables[0].display ])
      .toEqual([ undefined, false, true, "no known range" ]);
  });

  test("a known range is not marked dropped", () => {
    const { variables } = snapshotRows(stepWith({
      variables: [ { name: "a", value: FIRST, kind: "symbol" } ],
      symbolRanges: [ { symbol: FIRST, range: "{ [0, 0] }" } ],
      pathCondition: [ { symbol: FIRST, range: "{ [0, 5] }" } ]
    }));
    expect([ variables[0].range, variables[0].dropped ])
      .toEqual([ "{ [0, 0] }", false ]);
  });
});
