import { variableKey } from "@/components/Report/ConstraintTrace";
import {
  orderedRequirements, snapshotRows
} from "@/components/Report/SnapshotRows";

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
      path_condition: new Map([ [ SECOND, "added" ] ]),
      variables: new Map()
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
        path_condition: new Map(),
        variables: new Map()
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

  test("a variable's own change comes before its range's", () => {
    const { variables } = snapshotRows(stepWith({
      frame: 0,
      variables: [
        { name: "shift", value: "32", kind: "integer" },
        { name: "a", value: FIRST, kind: "symbol" },
        { name: "b", value: SECOND, kind: "symbol" }
      ],
      symbolRanges: [
        { symbol: FIRST, range: "{ [0, 0] }" },
        { symbol: SECOND, range: "{ [1, 1] }" }
      ],
      changes: {
        symbol_ranges: new Map([ [ FIRST, "narrowed" ], [ SECOND, "added" ] ]),
        path_condition: new Map(),
        variables: new Map([
          [ variableKey(0, "shift"), "added" ],
          [ variableKey(0, "b"), "changed" ]
        ])
      }
    }));
    expect(variables.map(row => [ row.name, row.change ])).toEqual(
      [ [ "a", "narrowed" ], [ "b", "changed" ], [ "shift", "added" ] ]);
  });
});

describe("where the rows were last set", () => {
  const at = node => ({ node, order: node + 1 });
  // `a` got FIRST at node 1, which was narrowed at node 4; `shift` got its
  // number at node 3; SECOND was assumed at node 2, and nothing tells of `k`.
  const SET = {
    variables: new Map([
      [ variableKey(0, "a"), at(1) ], [ variableKey(0, "shift"), at(3) ],
      [ variableKey(1, "a"), at(0) ]
    ]),
    path_condition: new Map([ [ FIRST, at(4) ], [ SECOND, at(2) ] ])
  };
  const step = stepWith({
    frame: 0,
    frames: [ { function: "top" }, { function: "inner", parent: 0 } ],
    variables: [
      { name: "a", value: FIRST, kind: "symbol" },
      { name: "k", value: "1", kind: "integer" },
      { name: "shift", value: "32", kind: "integer" }
    ],
    symbolRanges: [
      { symbol: FIRST, range: "{ [0, 0] }" },
      { symbol: SECOND, range: "{ [1, 1] }" }
    ],
    pathCondition: [
      { symbol: FIRST, range: "{ [0, 0] }" },
      { symbol: SECOND, range: "{ [1, 1] }" }
    ],
    lastSet: (list, key) => SET[list].get(key) ?? null
  });

  test("a variable by its value or what is assumed of it, the later", () => {
    const { variables, others, accumulated } = snapshotRows(step);
    expect(variables.map(row => [ row.name, row.changedAt?.node ])).toEqual(
      [ [ "a", 4 ], [ "k", undefined ], [ "shift", 3 ] ]);
    expect(others.map(row => row.changedAt.node)).toEqual([ 2 ]);
    expect(accumulated.map(row => row.changedAt.node)).toEqual([ 4, 2 ]);
  });

  test("a frame's variable is not another frame's of the same name", () => {
    const { callers } = snapshotRows({
      ...step,
      frame: 1,
      variables: [],
      callerVariables: [ { frame: 0, name: "a", value: "3", kind: "integer" } ]
    });
    expect(callers[0].rows[0].changedAt.node).toBe(1);
  });

  test("a step that tells no place has rows without one", () => {
    const { variables, accumulated } =
      snapshotRows({ ...step, lastSet: undefined });
    expect(variables.map(row => row.changedAt)).toEqual([ null, null, null ]);
    expect(accumulated.map(row => row.changedAt)).toEqual([ null, null ]);
  });

  test("the most recently set come first when asked, the rest by name", () => {
    const { variables, accumulated } = snapshotRows(step, "recent");
    expect(variables.map(row => row.name)).toEqual([ "a", "shift", "k" ]);
    expect(accumulated.map(row => row.symbol)).toEqual([ FIRST, SECOND ]);

    const late = { ...step, lastSet: (list, key) =>
      key === SECOND ? at(9) : SET[list].get(key) ?? null };
    expect(snapshotRows(late, "recent").accumulated.map(row => row.symbol))
      .toEqual([ SECOND, FIRST ]);
    expect(snapshotRows(late).accumulated.map(row => row.symbol))
      .toEqual([ FIRST, SECOND ]);
  });

  test("the requires list is ordered group by group", () => {
    const groups = [
      { key: "entry", rows: [
        { key: "x", changedAt: at(1) }, { key: "y", changedAt: at(5) },
        { key: "z", changedAt: at(5) }
      ] },
      { key: "call", rows: [ { key: "r", changedAt: at(9) } ] }
    ];
    const keys = order => orderedRequirements(groups, order).map(group =>
      [ group.key, group.rows.map(row => row.key) ]);
    expect(keys("recent")).toEqual(
      [ [ "entry", [ "y", "z", "x" ] ], [ "call", [ "r" ] ] ]);
    expect(keys("name")).toEqual(
      [ [ "entry", [ "x", "y", "z" ] ], [ "call", [ "r" ] ] ]);
  });
});
