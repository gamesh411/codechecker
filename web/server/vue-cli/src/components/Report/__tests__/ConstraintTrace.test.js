import {
  ConstraintTrace, compareNames, describeVariable, expressionLabel,
  labelPlace, parseSymbolExpression, readConstraintTrace, readableRange,
  shortenPaths, shortenStatement, symbolLabel, symbolType
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

// x is narrowed and y assumed at node 1, node 2 rewrites the whole range list
// and adds z, and node 3 adds z to the path condition. Steps 4 and 5 share
// node 3, step 2 has an event on its node, and step 7 goes back to node 2, as
// a printed path may.
const CHANGE_STRINGS = [
  "x", "{ [0, 10] }", "{ [0, 5] }", "y", "{ [1, 1] }", "z", "{ [2, 2] }"
];
const CHANGES = {
  version: 2,
  strings: CHANGE_STRINGS,
  steps: [
    { kind: "event", index: 0, order: 1, exploded_node: 0 },
    { kind: "control", index: 1, order: 2, exploded_node: 1 },
    { kind: "event", index: 1, order: 3, exploded_node: 1 },
    { kind: "control", index: 2, order: 4, exploded_node: 2 },
    { kind: "control", index: 3, order: 5, exploded_node: 3 },
    { kind: "control", index: 4, order: 6, exploded_node: 3 },
    { kind: "control", index: 5, order: 7, exploded_node: 2 }
  ],
  exploded_nodes: [
    {
      symbol_ranges: { insert: [ [ 0, 0, 1 ] ] },
      path_condition: { insert: [ [ 0, 0, 1 ] ] }
    },
    {
      symbol_ranges: { replace: [ [ 0, 0, 2 ] ], insert: [ [ 1, 3, 4 ] ] },
      path_condition: { replace: [ [ 0, 0, 2 ] ], insert: [ [ 1, 3, 4 ] ] }
    },
    {
      symbol_ranges: {
        remove: [ 0, 1 ],
        insert: [ [ 0, 0, 2 ], [ 1, 3, 4 ], [ 2, 5, 6 ] ]
      }
    },
    { path_condition: { insert: [ [ 2, 5, 6 ] ] } }
  ]
};

describe("changes at a step", () => {
  const changes = (trace, order) => {
    const { symbol_ranges, path_condition } = trace.changedAt(order);
    return {
      ranges: Object.fromEntries(symbol_ranges),
      condition: Object.fromEntries(path_condition)
    };
  };

  test("an entry is added or narrowed against the step before", () => {
    const trace = new ConstraintTrace(CHANGES);
    expect(changes(trace, 2)).toEqual({
      ranges: { x: "narrowed", y: "added" },
      condition: { x: "narrowed", y: "added" }
    });
  });

  test("steps showing one snapshot show one change", () => {
    const trace = new ConstraintTrace(CHANGES);
    expect(changes(trace, 3)).toEqual(changes(trace, 2));
  });

  test("a list rewritten whole reports only what really changed", () => {
    const trace = new ConstraintTrace(CHANGES);
    expect(changes(trace, 4)).toEqual({
      ranges: { z: "added" }, condition: {}
    });
  });

  test("the first step and an unknown step change nothing", () => {
    const trace = new ConstraintTrace(CHANGES);
    expect(changes(trace, 1)).toEqual({ ranges: {}, condition: {} });
    expect(changes(trace, 99)).toEqual({ ranges: {}, condition: {} });
    expect(changes(trace, -1)).toEqual({ ranges: {}, condition: {} });
  });

  test("a step after a jump back compares by symbol", () => {
    const trace = new ConstraintTrace(CHANGES);
    expect(changes(trace, 7)).toEqual({ ranges: {}, condition: {} });
  });

  test("the answer is built once", () => {
    const trace = new ConstraintTrace(CHANGES);
    expect(trace.changedAt(2)).toBe(trace.changedAt(2));
  });
});

describe("silent changes", () => {
  test("a control step changing the path condition without an event", () => {
    const trace = new ConstraintTrace(CHANGES);
    // Step 2 has an event on its node, step 4 only changes ranges, step 5
    // assumes z, step 6 repeats step 5's node, and step 7 goes back to a node
    // that assumes less than step 6.
    expect(trace.silentChanges()).toEqual([
      { order: 5, node: 3, afterEvent: 1 }
    ]);
    expect(trace.silentChanges()).toBe(trace.silentChanges());
  });

  test("a control step before any event follows none", () => {
    const trace = new ConstraintTrace({
      version: 2,
      strings: CHANGE_STRINGS,
      steps: [
        { kind: "control", index: 0, order: 1, exploded_node: 0 },
        { kind: "control", index: 1, order: 2, exploded_node: 1 },
        { kind: "event", index: 0, order: 3, exploded_node: 2 }
      ],
      exploded_nodes: [
        { path_condition: { insert: [ [ 0, 0, 1 ] ] } },
        { path_condition: { replace: [ [ 0, 0, 2 ] ] } },
        {}
      ]
    });
    expect(trace.silentChanges()).toEqual([
      { order: 2, node: 1, afterEvent: -1 }
    ]);
  });
});

// Two calls of get(): one in main, one in a callee the trace has no frame for,
// a malloc a checker modelled, and the parameter they are compared with.
const CALL = "conj_$2{int, LC1, S90, #1}";
const PRUNED = "conj_$5{int, LC3, S40, #1}";
const MODELLED = "conj_$7{void *, LC1, S120, #1}";
const SYMBOL_STRINGS = [
  CALL, "{ [42, 42] }", "get()", "get", "main", "reg_$0<int a>", "wrap",
  PRUNED, "malloc(4)", "malloc", MODELLED, "conj_$2"
];
const LOCATION = (line, col = 3) => ({ file: "/src/a.c", line, col });
const SYMBOLS = {
  version: 2,
  strings: SYMBOL_STRINGS,
  steps: [
    { kind: "event", index: 0, order: 1, exploded_node: 0 },
    { kind: "control", index: 0, order: 2, exploded_node: 1 },
    { kind: "event", index: 1, order: 3, exploded_node: 2 },
    { kind: "event", index: 2, order: 4, exploded_node: 3 }
  ],
  exploded_nodes: [
    { location: LOCATION(9), frame: 0 },
    { location: LOCATION(12), frame: 0 },
    { location: LOCATION(12), frame: 1 },
    { location: { ...LOCATION(13), file: "/src/b.h" }, frame: 1 }
  ],
  stack_frames: [ { function: "main" }, { function: "f", parent: 0 } ],
  symbols: [
    { symbol: 0, kind: "conjured", smt: 11, smt_signed: true, origin: {
      reason: "call_result", statement: 2, callee: 3, function: 4, frame: 0,
      location: LOCATION(11, 7)
    } },
    { symbol: 7, kind: "conjured", origin: {
      reason: "call_result", statement: 2, callee: 3, function: 6,
      location: LOCATION(30)
    } },
    { symbol: 10, kind: "conjured", origin: {
      reason: "other", statement: 8, callee: 9, function: 4, frame: 0,
      location: LOCATION(12, 12)
    } },
    { symbol: 5, kind: "region_value" }
  ]
};

describe("symbol table", () => {
  test("entries read back by dump with their strings", () => {
    const trace = new ConstraintTrace(SYMBOLS);
    expect(trace.symbol(CALL)).toEqual({
      kind: "conjured", smt: "conj_$2", smt_signed: true, origin: {
        reason: "call_result", statement: "get()", callee: "get",
        function: "main", frame: 0, location: LOCATION(11, 7)
      }
    });
    expect(trace.symbol("reg_$0<int a>")).toEqual({ kind: "region_value" });
    expect(trace.symbol("(conj_$2{int, LC1, S90, #1}) + 1")).toBeUndefined();
    expect(trace.symbols).toBe(trace.symbols);
  });

  test("a trace without the table has no entries", () => {
    const trace = new ConstraintTrace(TRACE);
    expect(trace.symbols.size).toBe(0);
    expect(trace.symbol("reg_$0<int a>")).toBeUndefined();
  });

  test("a call result reads as the callee's result at its line", () => {
    const trace = new ConstraintTrace(SYMBOLS);
    expect(symbolLabel(trace.symbol(CALL), 0)).toEqual({
      text: "result of get()", parts: [ "result of ", { code: "get()" } ],
      hint: "The analyzer did not see inside this call.",
      location: LOCATION(11, 7), frame: 0, function: null
    });
  });

  test("the creating function shows when it is another frame's", () => {
    const trace = new ConstraintTrace(SYMBOLS);
    expect(symbolLabel(trace.symbol(CALL), 1).function).toBe("main");
    // A frame the trace does not list is never the step's.
    expect(symbolLabel(trace.symbol(PRUNED), 0).function).toBe("wrap");
  });

  test("a call result without a known callee is named by its statement", () => {
    const entry = new ConstraintTrace(SYMBOLS).symbol(CALL);
    const indirect = { ...entry, origin: { ...entry.origin, callee: undefined,
      statement: "(*fp)(1)" } };
    expect(symbolLabel(indirect, 0).text).toBe("result of (*fp)(1)");
  });

  test("an unclassified or unknown reason names only its statement", () => {
    const entry = new ConstraintTrace(SYMBOLS).symbol(MODELLED);
    expect(symbolLabel(entry, 0)).toMatchObject({
      text: "value from malloc(4)",
      parts: [ "value from ", { code: "malloc(4)" } ],
      hint: "The analyzer did not record what kind of value this is.",
      location: LOCATION(12, 12)
    });
    expect(symbolLabel({ ...entry, origin: { ...entry.origin,
      reason: "something_new" } }, 0).text).toBe("value from malloc(4)");
  });

  test("a value without a statement reads as not recorded", () => {
    const entry = new ConstraintTrace(SYMBOLS).symbol(MODELLED);
    const bare = { ...entry, origin: { reason: "other", function: "main" } };
    expect(symbolLabel(bare, 0)).toMatchObject({
      text: "origin not recorded", parts: [ "origin not recorded" ],
      location: null
    });
  });

  test("symbols without an origin have no label", () => {
    const trace = new ConstraintTrace(SYMBOLS);
    expect(symbolLabel(trace.symbol("reg_$0<int a>"), 0)).toBeNull();
    expect(symbolLabel(undefined, 0)).toBeNull();
  });

  test("the nearest step to an origin is in its file and frame", () => {
    const trace = new ConstraintTrace(SYMBOLS);
    // Line 12 in frame 0 is nearer than line 9, and line 12 in frame 1 is
    // not the frame the value was made in.
    expect(trace.nearestStep(LOCATION(11, 7), 0)).toBe(2);
    // Without a frame of its own, any step in the file counts, and of two
    // equally near the event wins over the arrow.
    expect(trace.nearestStep(LOCATION(30), undefined)).toBe(3);
    expect(trace.nearestStep({ ...LOCATION(1), file: "/src/b.h" }, 0))
      .toBe(4);
    expect(trace.nearestStep({ ...LOCATION(1), file: "/src/c.c" }, 0))
      .toBeNull();
    expect(trace.nearestStep(undefined, 0)).toBeNull();
  });

  test("two origins on one line are told apart by their column", () => {
    const trace = new ConstraintTrace({
      ...SYMBOLS,
      exploded_nodes: [
        { location: LOCATION(12, 3), frame: 0 },
        { location: LOCATION(12, 20), frame: 0 },
        { location: LOCATION(12, 35), frame: 0 },
        { location: LOCATION(13, 7), frame: 0 }
      ]
    });
    // Steps 1 to 4 are on nodes 0 to 3.
    expect(trace.nearestStep(LOCATION(12, 21), 0)).toBe(2);
    expect(trace.nearestStep(LOCATION(12, 4), 0)).toBe(1);
    expect(trace.nearestStep(LOCATION(12, 90), 0)).toBe(3);
    // An event wins over the arrow into the same place, whichever is first.
    const arrow = new ConstraintTrace({
      ...SYMBOLS,
      steps: [
        { kind: "control", index: 0, order: 1, exploded_node: 0 },
        { kind: "event", index: 0, order: 2, exploded_node: 1 },
        { kind: "control", index: 1, order: 3, exploded_node: 1 }
      ],
      exploded_nodes: [
        { location: LOCATION(12, 3), frame: 0 },
        { location: LOCATION(12, 3), frame: 0 }
      ]
    });
    expect(arrow.nearestStep(LOCATION(12, 3), 0)).toBe(2);
    // A nearer line wins over a nearer column.
    expect(trace.nearestStep(LOCATION(13, 90), 0)).toBe(4);
  });
});

const REPLACED = "conj_$4{int, LC1, S50, #1}";
const FIELD = "derived_$5{conj_$4{int, LC1, S50, #1},Tok.Kind}";
const WIDENED = "conj_$6{int, LC1, S60, #3}";
const GLOBALS = "conj_$7{int, LC1, S70, #1}";
const GLOBAL = "derived_$8{conj_$7{int, LC1, S70, #1},counter}";
const RESULT_FIELD = "derived_$9{conj_$2{int, LC1, S90, #1},r.len}";
const LONG_CALL = "Lex(Tok, /*Recover=*/true, getLangOpts().CPlusPlus)";
const REPLACING_STRINGS = [
  REPLACED, "Lex(Tok)", "Lex", "f", "Tok", FIELD, "Tok.Kind", WIDENED, "i",
  "x", GLOBALS, "refresh()", "refresh", GLOBAL, "counter", RESULT_FIELD,
  "r.len", CALL, "get()", "get", LONG_CALL
];
const REPLACING = {
  version: 2,
  strings: REPLACING_STRINGS,
  steps: [ { kind: "event", index: 0, order: 1, exploded_node: 0 } ],
  exploded_nodes: [ { location: LOCATION(9), frame: 0 } ],
  stack_frames: [ { function: "f" } ],
  symbols: [
    { symbol: 0, kind: "conjured", origin: {
      reason: "invalidation", statement: 1, callee: 2, region: 4,
      function: 3, frame: 0, location: LOCATION(57)
    } },
    { symbol: 5, kind: "derived", parent: 0, region: 6 },
    { symbol: 7, kind: "conjured", origin: {
      reason: "loop_widening", statement: 8, region: 9, function: 3,
      frame: 0, location: LOCATION(6, 19), loop: LOCATION(6, 3)
    } },
    { symbol: 10, kind: "conjured", origin: {
      reason: "invalidation", statement: 11, callee: 12, globals: true,
      function: 3, frame: 0, location: LOCATION(20)
    } },
    { symbol: 13, kind: "derived", parent: 10, region: 14 },
    { symbol: 15, kind: "derived", parent: 17, region: 16 },
    { symbol: 17, kind: "conjured", origin: {
      reason: "call_result", statement: 18, callee: 19, function: 3,
      frame: 0, location: LOCATION(30)
    } }
  ]
};

describe("labels of replaced values", () => {
  const trace = new ConstraintTrace(REPLACING);
  const label = (dump, frame = 0) =>
    symbolLabel(trace.symbol(dump), frame, d => trace.symbol(d));

  test("the table reads a derived symbol's parent and region", () => {
    expect(trace.symbol(FIELD)).toEqual(
      { kind: "derived", parent: REPLACED, region: "Tok.Kind" });
    expect(trace.symbol(REPLACED).origin.region).toBe("Tok");
  });

  test("memory a call replaced names the memory and the call", () => {
    expect(label(REPLACED)).toEqual({
      text: "Tok after Lex(Tok) may have changed it",
      parts: [ { code: "Tok" }, " after ", { code: "Lex(Tok)" },
        " may have changed it" ],
      hint: "The analyzer did not see inside Lex(Tok), so it treats the " +
        "memory the call can reach as changed.",
      location: LOCATION(57), frame: 0, function: null
    });
  });

  test("a field of replaced memory reads as its access path", () => {
    expect(label(FIELD)).toMatchObject({
      text: "Tok.Kind after Lex(Tok) may have changed it",
      location: LOCATION(57), frame: 0, function: null
    });
    expect(label(FIELD, 1).function).toBe("f");
  });

  test("globals a call replaced read as a global", () => {
    expect(label(GLOBALS)).toMatchObject({
      text: "a global after refresh() may have changed it",
      hint: "The analyzer did not see inside refresh(), so it treats " +
        "every global as changed."
    });
    expect(label(GLOBAL).text)
      .toBe("counter after refresh() may have changed it");
  });

  test("a widened loop's value names the loop's line", () => {
    expect(label(WIDENED)).toMatchObject({
      text: "x after the loop may have changed it",
      hint: "The analyzer stopped following this loop after a few passes, " +
        "so it treats the function's variables and globals as changed by it.",
      location: LOCATION(6, 3), frame: 0
    });
  });

  test("a field of a call result reads as part of that result", () => {
    expect(label(RESULT_FIELD)).toMatchObject({
      text: "r.len of the result of get()", location: LOCATION(30)
    });
  });

  test("without a region, the memory and the parts are said plainly", () => {
    const origin = { ...trace.symbol(REPLACED).origin, region: undefined };
    const parent = { ...trace.symbol(REPLACED), origin };
    expect(symbolLabel(parent, 0).text)
      .toBe("a value after Lex(Tok) may have changed it");
    expect(symbolLabel({ kind: "derived", parent: REPLACED }, 0,
      d => trace.symbol(d)).text)
      .toBe("part of Tok after Lex(Tok) may have changed it");
  });

  test("a part of a value without a label is its access path or nothing",
    () => {
      const lookup = () => ({ kind: "region_value" });
      expect(symbolLabel({ kind: "derived", parent: "reg_$0<struct S s>",
        region: "s.x" }, 0, lookup)).toMatchObject({
        text: "s.x", parts: [ { code: "s.x" } ], location: null
      });
      expect(symbolLabel({ kind: "derived", parent: "reg_$0<struct S s>" },
        0, lookup)).toBeNull();
      expect(symbolLabel(trace.symbol(FIELD), 0)).toMatchObject(
        { text: "Tok.Kind" });
    });

  test("a long call is shortened, with the whole call in the hint", () => {
    const origin = { ...trace.symbol(REPLACED).origin, statement: LONG_CALL };
    const shown = symbolLabel({ kind: "conjured", origin }, 0);
    expect(shown.text).toBe("Tok after Lex(…) may have changed it");
    expect(shown.hint).toContain(LONG_CALL);
  });
});

describe("symbolic expressions", () => {
  const trace = new ConstraintTrace(REPLACING);
  const lookup = dump => trace.symbol(dump);
  const label = (dump, names = {}) =>
    expressionLabel(dump, 0, lookup, symbol => names[symbol]);
  const WIDENED_ATOM = "conj_$6{int, LC1, S60, #3}";

  test("an expression reads from the labels of its atoms", () => {
    expect(label(`(${CALL}) + 1`)).toEqual({
      text: "result of get() + 1",
      parts: [ "result of ", { code: "get()" }, " + 1" ],
      hint: "Worked out from the values named here.",
      location: null, frame: undefined, function: null
    });
  });

  test("a region value is named by its variable or by its region", () => {
    const dump = `(${WIDENED_ATOM}) > (reg_$1<unsigned int n>)`;
    expect(label(dump).text)
      .toBe("x after the loop may have changed it > n");
    expect(label(dump, { "reg_$1<unsigned int n>": "count" }).text)
      .toBe("x after the loop may have changed it > count");
    expect(label(dump).parts).toEqual([
      { code: "x" }, " after the loop may have changed it > ", { code: "n" }
    ]);
  });

  test("integers lose their suffix, casts and negation are told", () => {
    expect(label(`(${CALL}) & 7U`).text).toBe("result of get() & 7");
    expect(label(`(int) (${CALL})`).text).toBe("result of get() as int");
    expect(label(`-${CALL}`).text).toBe("-result of get()");
    expect(label(`5 - (${CALL})`).text).toBe("5 - result of get()");
    expect(label("(unsigned long) (reg_$1<int n>)").text)
      .toBe("n as unsigned long");
  });

  test("an operator inside another keeps its brackets", () => {
    expect(label(`((${CALL}) + 1) * (reg_$1<int n>)`).text)
      .toBe("(result of get() + 1) * n");
    expect(label(`((${CALL}) + 1) + (reg_$1<int n>)`).text)
      .toBe("result of get() + 1 + n");
    expect(label(`(int) ((${CALL}) + 1)`).text)
      .toBe("(result of get() + 1) as int");
  });

  test("an expression it cannot read to the end stays raw", () => {
    [
      `(${CALL}) + `, `(${CALL}`, `${CALL}) + 1`, `(${CALL}) ?? 1`,
      `(${CALL}) + 1 + 2`, "(reg_$1<int n>) + (reg_$2<int", "()",
      // An atom nothing can name: no label and not a region value.
      "(extent_$3{SymRegion{reg_$1<char * p>}}) + 1",
      `(${CALL}) + (conj_$99{int, LC1, S1, #1})`,
      // A region value with no readable name.
      "(reg_$1<int Element{p,0 S64b,int}>) + 1",
      "", "not a symbol"
    ].forEach(dump => {
      expect(label(dump)).toBeNull();
    });
  });

  test("an atom alone is no expression", () => {
    expect(label(CALL)).toBeNull();
    expect(parseSymbolExpression(CALL)).toEqual({ atom: CALL });
    expect(parseSymbolExpression(undefined)).toBeNull();
  });

  test("the tree keeps what the dump says", () => {
    expect(parseSymbolExpression("(reg_$1<int n>) - -3")).toEqual({
      operator: "-", left: { atom: "reg_$1<int n>" }, right: { integer: "-3" }
    });
    expect(parseSymbolExpression("4U << (reg_$1<int n>)")).toEqual({
      operator: "<<", left: { integer: "4" }, right: { atom: "reg_$1<int n>" }
    });
    // Brackets inside an atom, a type in a cast and an arrow in a region.
    expect(parseSymbolExpression(
      "(void (*)(int)) (conj_$2{int (*)(int), LC1, S2, #1})"))
      .toEqual({ cast: "void (*)(int)",
        operand: { atom: "conj_$2{int (*)(int), LC1, S2, #1}" } });
    expect(parseSymbolExpression("(reg_$1<int p->x>) + 1").left)
      .toEqual({ atom: "reg_$1<int p->x>" });
  });
});

describe("statement text", () => {
  test("a short statement stays whole", () => {
    expect(shortenStatement("get()", "get")).toBe("get()");
    expect(shortenStatement("x".repeat(40), undefined)).toBe("x".repeat(40));
  });

  test("a long call keeps what it calls and drops its arguments", () => {
    expect(shortenStatement(
      "this->parse(prec::LogicalOr, /*Recover=*/true, Opts.Strict)", "parse"))
      .toBe("this->parse(\u2026)");
    expect(shortenStatement(LONG_CALL, "Lex")).toBe("Lex(\u2026)");
  });

  test("a call on a long receiver is shortened to its callee", () => {
    expect(shortenStatement(
      "Context.getTranslationUnitDecl()->getASTContext().lookup(Name, Kind)",
      "lookup")).toBe("lookup(\u2026)");
  });

  test("what is not a call is cut", () => {
    const cut = shortenStatement(
      "State.Table[Index] + State.Offset * Scale + Event");
    expect(cut).toHaveLength(40);
    expect(cut.endsWith("\u2026")).toBe(true);
    // An indirect call keeps what it calls through.
    expect(shortenStatement("(*callbacks[Index])(State, Event, Argument)"))
      .toBe("(*callbacks[Index])(\u2026)");
    expect(shortenStatement("make<Tag, decltype(f(1))>(State, Event, Argument)",
      "make")).toBe("make<Tag, decltype(f(1))>(\u2026)");
  });

  test("nothing stays nothing", () => {
    expect(shortenStatement(undefined)).toBeUndefined();
    expect(shortenStatement("")).toBe("");
  });
});

describe("types of symbols", () => {
  const pointer = { type: "int *", pointer: true };
  const unsigned = { type: "unsigned int", pointer: false };

  test("the table reads a symbol's type and pointer-ness", () => {
    const trace = new ConstraintTrace({
      ...SYMBOLS,
      strings: [ ...SYMBOL_STRINGS, "unsigned long" ],
      symbols: [
        { symbol: 0, kind: "conjured", type: 12, pointer: true },
        { symbol: 7, kind: "conjured" }
      ]
    });
    expect(symbolType(trace.symbol(CALL)))
      .toEqual({ type: "unsigned long", pointer: true });
    expect(trace.symbol(CALL)).toMatchObject({ pointer: true });
    expect(symbolType(trace.symbol(PRUNED))).toBeUndefined();
    expect(symbolType(undefined)).toBeUndefined();
  });

  test("a pointer reads as null or non-null from its entry", () => {
    // Derived symbols have no type in their dump.
    const derived = "derived_$9{conj_$4{int, LC1, S50, #1},p.next}";
    expect(readableRange("{ [0, 0] }", derived, pointer)).toBe("null");
    expect(readableRange("{ [1, 18446744073709551615] }", derived, pointer))
      .toBe("non-null");
    expect(readableRange("{ [1, 4294967295] }", derived, pointer))
      .toBe("non-null");
    expect(readableRange("{ [8, 8] }", derived, pointer)).toBe("8");
    expect(readableRange("{ [5, 18446744073709551615] }", derived, pointer))
      .toBeNull();
  });

  test("the dump tells a pointer when the table does not", () => {
    expect(readableRange("{ [0, 0] }", "reg_$1<int * p>")).toBe("null");
    expect(readableRange("{ [1, 18446744073709551615] }",
      "conj_$2{void *, LC1, S2, #1}")).toBe("non-null");
    expect(readableRange("{ [3, 4294967295] }", "reg_$1<unsigned int n>"))
      .toBe("at least 3");
    expect(readableRange("{ [1, 4294967295] }", "reg_$1<unsigned int n>"))
      .toBe("not 0");
    // The table wins over the dump.
    expect(readableRange("{ [0, 0] }", "reg_$1<int * p>", unsigned))
      .toBe("0");
  });

  test("the type names the width of a derived or typedef'd integer", () => {
    const derived = "derived_$9{conj_$4{int, LC1, S50, #1},t.Kind}";
    expect(readableRange("{ [3, 4294967295] }", derived, unsigned))
      .toBe("at least 3");
    expect(readableRange("{ [-2147483648, 9] }", derived,
      { type: "int", pointer: false })).toBe("at most 9");
    expect(readableRange("{ [3, 18446744073709551615] }", derived,
      { type: "unsigned long", pointer: false })).toBe("at least 3");
    // Neither the dump nor the table says: left alone.
    expect(readableRange("{ [3, 4294967295] }", derived)).toBeNull();
    expect(readableRange("{ [3, 4294967295] }", derived,
      { type: "float", pointer: false })).toBeNull();
    expect(readableRange("{ [3, 4294967295] }", derived,
      { type: "char", pointer: false })).toBeNull();
  });

  test("a variable is described by its range and the entry's type", () => {
    const snapshot = { symbol_ranges: [
      { symbol: "derived_$9{c,p.next}", range: "{ [1, 18446744073709551615] }" }
    ] };
    const variable = { name: "q", value: "derived_$9{c,p.next}",
      kind: "symbol" };
    expect(describeVariable(variable, snapshot, { type: "int *",
      pointer: true })).toEqual({
      text: "non-null", raw: "{ [1, 18446744073709551615] }"
    });
    expect(describeVariable(variable, snapshot).text)
      .toBe("{ [1, 18446744073709551615] }");
    expect(describeVariable({ ...variable, value: "x" }, snapshot))
      .toEqual({ text: "(symbolic)", raw: "(symbolic)" });
    expect(describeVariable({ name: "k", value: "5", kind: "integer" },
      snapshot)).toEqual({ text: "5", raw: "5" });
  });
});

describe("the same call twice", () => {
  const FIRST = "conj_$10{int, LC1, S11, #1}";
  const SECOND = "conj_$12{int, LC1, S13, #1}";
  const THIRD = "conj_$14{int, LC1, S15, #1}";
  const MODELLED_SAME = "conj_$16{int, LC1, S17, #1}";
  const strings = [
    FIRST, "Cells.size()", "size", "f", SECOND, THIRD, MODELLED_SAME,
    "a_much_longer_statement_that_was_cut_by_clang_at_the...", "other()"
  ];
  const table = [
    { symbol: 0, kind: "conjured", origin: { reason: "call_result",
      statement: 1, callee: 2, function: 3, frame: 0, location: LOCATION(8) } },
    { symbol: 4, kind: "conjured", origin: { reason: "call_result",
      statement: 1, callee: 2, function: 3, frame: 0,
      location: LOCATION(12) } },
    { symbol: 5, kind: "conjured", origin: { reason: "call_result",
      statement: 1, callee: 2, function: 3, location: LOCATION(30) } },
    // A checker modelled it: not known to be a call result.
    { symbol: 6, kind: "conjured", origin: { reason: "other", statement: 1,
      callee: 2, function: 3, frame: 0, location: LOCATION(9) } },
    { symbol: 7, kind: "conjured", origin: { reason: "call_result",
      statement: 7, function: 3 } },
    { symbol: 8, kind: "conjured", origin: { reason: "call_result",
      statement: 8, function: 3 } }
  ];
  const build = symbols => new ConstraintTrace({
    ...SYMBOLS, strings, symbols
  });

  test("lists the other results of one call text", () => {
    const trace = build(table);
    expect(trace.sameCall(FIRST).map(call => call.symbol))
      .toEqual([ SECOND, THIRD ]);
    expect(trace.sameCall(SECOND).map(call => call.symbol))
      .toEqual([ FIRST, THIRD ]);
    expect(trace.sameCall(FIRST)[0].origin.location).toEqual(LOCATION(12));
  });

  test("a lone call, a modelled one and an unknown symbol have none", () => {
    const trace = build(table);
    expect(trace.sameCall(MODELLED_SAME)).toEqual([]);
    expect(trace.sameCall("conj_$8")).toEqual([]);
    expect(trace.sameCall(undefined)).toEqual([]);
    expect(build(table.slice(0, 1)).sameCall(FIRST)).toEqual([]);
  });

  test("statements Clang cut are not compared", () => {
    const cut = "a_much_longer_statement_that_was_cut_by_clang_at_the...";
    const trace = build([
      { symbol: 0, kind: "conjured", origin: { reason: "call_result",
        statement: 7, function: 3 } },
      { symbol: 4, kind: "conjured", origin: { reason: "call_result",
        statement: 7, function: 3 } }
    ]);
    expect(cut.endsWith("...")).toBe(true);
    expect(trace.sameCall(FIRST)).toEqual([]);
  });
});

describe("a location whose file is not a path", () => {
  const label = (file, otherFile) =>
    ({ location: { file, line: 7, col: 1 }, otherFile });

  test("is told by its line alone", () => {
    expect(labelPlace(label(3, true))).toBe("line 7");
    expect(labelPlace(label(undefined, true))).toBe("line 7");
    expect(labelPlace(label(null, false))).toBe("line 7");
  });

  test("a path names its file only when it is another file", () => {
    expect(labelPlace(label("/src/dir/a.c", true))).toBe("a.c:7");
    expect(labelPlace(label("/src/dir/a.c", false))).toBe("line 7");
    expect(labelPlace({ location: null })).toBeNull();
  });

  test("shortening paths takes text only", () => {
    expect(shortenPaths(3)).toBe("");
    expect(shortenPaths(undefined)).toBe("");
    expect(shortenPaths("in /a/b/c.h")).toBe("in c.h");
  });

  test("the nearest step of a numbered file is a step of that number", () => {
    const trace = new ConstraintTrace({
      ...SYMBOLS,
      exploded_nodes: SYMBOLS.exploded_nodes.map(node =>
        ({ ...node, location: { ...node.location, file: 0 } }))
    });
    expect(trace.nearestStep({ file: 0, line: 12, col: 1 }, 0)).toBe(2);
    expect(trace.nearestStep({ file: 1, line: 12, col: 1 }, 0)).toBeNull();
  });
});
