import {
  ConstraintTrace, functionName, readConstraintTrace
} from "@/components/Report/ConstraintTrace";

// What the server resolves at every step of every constraint trace fixture,
// written by the server's unit test test_what_if_names.py from the server's
// own code. The page offers its own reading of the same traces, so this is
// where the two are compared.
import EXPECTED from
  "../../../../../../tests/projects/constraint_trace/what_if_names.json";

function lines(names) {
  return names.map(({ name, values }) =>
    `${name} = ${values.map(value => `${value.kind}:${value.value}`)
      .sort().join(" | ")}`);
}

describe("What-If names", () => {
  Object.entries(EXPECTED).forEach(([ run, expected ]) => {
    test(`the page offers what the server resolves in ${run}`, () => {
      const trace = readConstraintTrace(expected.trace);
      expect(trace).toBeInstanceOf(ConstraintTrace);
      [ [ "anchored", false ], [ "throughout", true ] ].forEach(
        ([ kind, throughout ]) => {
          Object.entries(expected[kind]).forEach(([ order, names ]) => {
            expect([ kind, order, lines(
              trace.whatIfNames(Number(order), throughout)) ])
              .toEqual([ kind, order, names ]);
          });
        });
    });
  });

  test("the fixtures name callers, access paths and both", () => {
    const names = new Set(Object.values(EXPECTED).flatMap(run =>
      Object.values(run.anchored).flat().map(line => line.split(" = ")[0])));
    [ "top::n", "middle::m", "line->next->cells[1].flags", "*g" ].forEach(
      name => expect(names).toContain(name));
  });

  test("a function is written without its anonymous namespace", () => {
    expect(functionName("clang::(anonymous namespace)::Parser::parse"))
      .toBe("clang::Parser::parse");
    expect(functionName(undefined)).toBe("");
  });
});
