import {
  ConstraintTrace, compareNames, readConstraintTrace
} from "@/components/Report/ConstraintTrace";

// Three nodes: `a` narrowed to 0, then `b` assumed non-zero while `a` is
// dropped from the known ranges, then a node whose shown lists equal the
// second one's. `p` changes from a symbol to a concrete value on the way.
const STRINGS = [
  "reg_$0<int a>", "{ [0, 0] }", "a", "symbol", "reg_$0",
  "(assert (= reg_$0 #x00000000))", "(declare-fun reg_$0 () (_ BitVec 32))",
  "reg_$1<int b>", "{ [1, 2] }", "b", "reg_$1",
  "(assert (not (= reg_$1 #x00000000)))", "p", "0", "integer"
];
const TRACE = {
  version: 2,
  smtlib: true,
  strings: STRINGS,
  steps: [
    { kind: "event", index: 0, order: 3, exploded_node: 0 },
    { kind: "control", index: 1, order: 4, exploded_node: 1 },
    { kind: "event", index: 1, order: 5, exploded_node: 1 },
    { kind: "event", index: 2, order: 9, exploded_node: 2 }
  ],
  exploded_nodes: [
    {
      id: 7, kind: "PostStmt",
      cfg_position: { cfg: 0, block: 2, element: 1 },
      symbol_ranges: { insert: [ [ 0, 0, 1 ] ] },
      variables: { insert: [ [ 0, 2, 0, 3, 4, 1 ] ] },
      path_condition: { insert: [ [ 0, 0, 1, 5 ] ] },
      smt_declarations: { insert: [ [ 0, 6 ] ] }
    },
    {
      id: 7, kind: "BlockEdge",
      symbol_ranges: { remove: [ 0 ], insert: [ [ 0, 7, 8 ] ] },
      variables: { insert: [ [ 1, 9, 7, 3, 10, 1 ], [ 2, 12, 13, 14 ] ] },
      path_condition: { insert: [ [ 1, 7, 8, 11 ] ] },
      smt_declarations: { insert: [ [ 1, 6 ] ] }
    },
    { id: 9, kind: "PreLoad" }
  ],
  cfgs: [ {
    function: "f", entry: 2, exit: 0,
    blocks: [ [ 2, "", [ 1 ], [] ],
      [ 1, "if a", [ 0, -1 ], [ [ "a", 3, -1 ], [ "a (cast)", 3, 0 ] ] ],
      [ 0, "", [], [] ] ],
    path_blocks: [ 1, 2 ],
    path_edges: [ 2, 1, 1, 0 ]
  } ]
};

describe("ConstraintTrace", () => {
  test("a node's snapshot reads back whole", () => {
    const trace = new ConstraintTrace(TRACE);
    expect(trace.snapshot(1)).toEqual({
      symbol_ranges: [ { symbol: "reg_$1<int b>", range: "{ [1, 2] }" } ],
      variables: [
        { name: "a", value: "reg_$0<int a>", kind: "symbol", smt: "reg_$0",
          smt_signed: true },
        { name: "b", value: "reg_$1<int b>", kind: "symbol", smt: "reg_$1",
          smt_signed: true },
        { name: "p", value: "0", kind: "integer" }
      ],
      path_condition: [
        { symbol: "reg_$0<int a>", range: "{ [0, 0] }" },
        { symbol: "reg_$1<int b>", range: "{ [1, 2] }" }
      ],
      smt_declarations: [ STRINGS[6], STRINGS[6] ],
      smt_assertions: [ STRINGS[5], STRINGS[11] ],
      exploded_node: 1
    });
    expect(trace.snapshot(0).cfg_position).toEqual(
      { cfg: 0, block: 2, element: 1 });
    expect(trace.snapshot(1)).toBe(trace.snapshot(1));
  });

  test("snapshots compare without being built", () => {
    const trace = new ConstraintTrace(TRACE);
    expect(trace.sameSnapshot(1, 2)).toBe(true);
    expect(trace.sameSnapshot(0, 1)).toBe(false);
    expect(trace.sameSnapshot(0, 0)).toBe(true);
  });

  test("symbols keep the name they last had", () => {
    const trace = new ConstraintTrace(TRACE);
    expect([ ...trace.symbolNames(3) ]).toEqual([
      [ "reg_$0<int a>", "a" ] ]);
    expect([ ...trace.symbolNames(9) ]).toEqual([
      [ "reg_$0<int a>", "a" ], [ "reg_$1<int b>", "b" ] ]);
    expect([ ...trace.symbolNames(-1) ]).toEqual([]);
  });

  test("control flow graphs read back as objects, once", () => {
    const trace = new ConstraintTrace(TRACE);
    expect(trace.cfgs[0].blocks[1]).toEqual({
      id: 1, terminator: "if a", successors: [ 0, -1 ],
      elements: [ { text: "a", line: 3 },
        { text: "a (cast)", line: 3, parent: 0 } ]
    });
    expect(trace.cfgs[0].path_edges).toEqual(
      [ { from: 2, to: 1 }, { from: 1, to: 0 } ]);
    expect(trace.cfgs).toBe(trace.cfgs);
  });

  test("a stored trace of an older format keeps only its version", () => {
    expect(readConstraintTrace(JSON.stringify({ version: 1, steps: [] })))
      .toEqual({ version: 1 });
    expect(readConstraintTrace(JSON.stringify(TRACE)))
      .toBeInstanceOf(ConstraintTrace);
    expect(readConstraintTrace(null)).toBe(null);
    const warn = jest.spyOn(console, "warn").mockImplementation(() => {});
    expect(readConstraintTrace(JSON.stringify({ version: 2 }))).toBe(null);
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });
});

describe("compareNames", () => {
  it("orders the numbers in names by value", () => {
    expect([ "a100", "a10", "b", "a2", "a1", "a0" ].sort(compareNames))
      .toEqual([ "a0", "a1", "a2", "a10", "a100", "b" ]);
  });
});
