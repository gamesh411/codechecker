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
});
