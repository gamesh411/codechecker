import {
  ConstraintTrace, compareNames, readConstraintTrace, readableRange,
  shortenPaths
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

describe("shortenPaths", () => {
  it("cuts an absolute path to its file name", () => {
    expect(shortenPaths("const class (lambda at /a/b/c.cpp:3:12)"))
      .toBe("const class (lambda at c.cpp:3:12)");
  });

  it("leaves a division and an empty text alone", () => {
    expect(shortenPaths("a / b")).toBe("a / b");
    expect(shortenPaths(undefined)).toBe("");
  });
});

describe("readableRange", () => {
  const INT = "reg_$1<int a>";
  const UINT = "reg_$2<unsigned int n>";

  it("reads a range that excludes one value as not N", () => {
    expect(readableRange("{ [-2147483648, -1] [1, 2147483647] }", INT))
      .toBe("not 0");
    expect(readableRange("{ [0, 4] [6, 4294967295] }", UINT)).toBe("not 5");
    expect(readableRange("{ [1, 4294967295] }", UINT)).toBe("not 0");
    expect(readableRange("{ [0, 65534] }", "reg_$3<unsigned short s>"))
      .toBe("not 65535");
  });

  it("reads the analyzer's comma between intervals", () => {
    expect(readableRange("{ [-2147483648, -1], [1, 2147483647] }", INT))
      .toBe("not 0");
  });

  it("reads a single value as the value, whatever the type", () => {
    expect(readableRange("{ [1, 1] }", INT)).toBe("1");
    expect(readableRange("{ [-5, -5] }", "reg_$4<size_t n>")).toBe("-5");
    expect(readableRange("{ [7, 7] }", undefined)).toBe("7");
  });

  it("reads a range up to the type's maximum as at least N", () => {
    expect(readableRange("{ [5, 2147483647] }", INT)).toBe("at least 5");
    expect(readableRange("{ [-3, 127] }", "reg_$5<signed char c>"))
      .toBe("at least -3");
    expect(readableRange("{ [10, 65535] }", "reg_$6<unsigned short s>"))
      .toBe("at least 10");
    expect(readableRange("{ [2, 18446744073709551615] }",
      "reg_$7<unsigned long long n>")).toBe("at least 2");
    expect(readableRange("{ [2, 9223372036854775807] }",
      "conj_$8{long, LC1, S1, #1}")).toBe("at least 2");
  });

  it("reads a range from the type's minimum as at most N", () => {
    expect(readableRange("{ [-2147483648, 9] }", INT)).toBe("at most 9");
    expect(readableRange("{ [-32768, -1] }", "reg_$9<short s>"))
      .toBe("at most -1");
    expect(readableRange("{ [0, 100] }", "reg_$10<unsigned char c>"))
      .toBe("at most 100");
    expect(readableRange(
      "{ [-170141183460469231731687303715884105728, 0] }",
      "reg_$11<__int128 w>")).toBe("at most 0");
  });

  it("reads the type from a const-qualified or conjured dump", () => {
    expect(readableRange("{ [5, 2147483647] }", "reg_$1<const int a>"))
      .toBe("at least 5");
    expect(readableRange("{ [5, 2147483647] }", "conj_$2{int, LC1, S2, #1}"))
      .toBe("at least 5");
  });

  it("leaves a range alone when the type cannot be told", () => {
    const _range = "{ [5, 2147483647] }";
    // A typedef, an enum, a plain char, a pointer, an expression and no symbol.
    [ "reg_$1<size_t n>", "reg_$1<enum Color c>", "reg_$1<char c>",
      "reg_$1<int * p>", "(reg_$1<int a>) + 1", "(long) (reg_$1<int a>)",
      "reg_$1<float f>", undefined ].forEach(symbol =>
      expect(readableRange(_range, symbol)).toBe(null));
  });

  it("leaves a long alone unless its width is certain from the range", () => {
    // 2^31 - 1 is the maximum of a 32-bit long as well as an int.
    expect(readableRange("{ [5, 2147483647] }", "reg_$1<long l>"))
      .toBe(null);
    expect(readableRange("{ [5, 9223372036854775807] }", "reg_$1<long l>"))
      .toBe("at least 5");
  });

  it("leaves any other range alone", () => {
    [ "{ [3, 9] }", "{ [-2147483648, 2147483647] }",
      "{ [-2147483648, 0] [5, 9] }", "{ [0, 0] [2, 2] [4, 2147483647] }",
      "{ [1, 4294967295] }", "{ }", "nonsense" ].forEach(range =>
      expect(readableRange(range, INT)).toBe(null));
    // A range outside the type means the type was not read right.
    expect(readableRange("{ [1, 4294967295] }", INT)).toBe(null);
  });
});
