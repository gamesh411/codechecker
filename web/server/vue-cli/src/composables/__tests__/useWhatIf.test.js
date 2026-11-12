import { useWhatIf } from "@/composables/useWhatIf";

// Each call to checkWhatIf is kept, so a test answers it when it chooses.
const calls = [];
jest.mock("@cc-api", () => ({
  ccService: {
    getClient: () => ({
      checkWhatIf: (reportId, constraints, withExample, callback) =>
        calls.push({ constraints, withExample, callback })
    })
  },
  handleThriftError: callback => callback
}), { virtual: true });

jest.mock("@cc/report-server-types", () => ({
  WhatIfConstraint: class {
    constructor(fields) { Object.assign(this, fields); }
  }
}), { virtual: true });

const EXAMPLE = {
  verdict: 0,
  example: [ { symbol: "reg_$1<int n>", value: "-1" } ]
};

describe("useWhatIf example", () => {
  beforeEach(() => { calls.length = 0; });

  it("asks for it only when told to", () => {
    const _whatIf = useWhatIf();
    _whatIf.check({}, { order: 1, expression: "n < 0" });
    expect(calls[0].withExample).toBe(false);

    calls[0].callback({ verdict: 0 });
    _whatIf.askExample({});
    expect(calls[1].withExample).toBe(true);
    expect(calls[1].constraints.map(c => c.expression)).toEqual([ "n < 0" ]);
    expect(_whatIf.example.value.loading).toBe(true);

    calls[1].callback(EXAMPLE);
    expect(_whatIf.example.value.values.get("reg_$1<int n>")).toBe("-1");
  });

  it("is dropped when the list changes", () => {
    const _whatIf = useWhatIf();
    _whatIf.check({}, { order: 1, expression: "n < 0" });
    calls[0].callback({ verdict: 0 });
    _whatIf.askExample({});
    calls[1].callback(EXAMPLE);

    // Checking the same list again keeps it.
    _whatIf.check({});
    calls[2].callback({ verdict: 0 });
    expect(_whatIf.example.value.values).toBeTruthy();

    _whatIf.check({}, { order: 1, expression: "n > -5" });
    calls[3].callback({ verdict: 0 });
    expect(_whatIf.example.value).toBeNull();

    _whatIf.askExample({});
    calls[4].callback(EXAMPLE);
    _whatIf.remove(_whatIf.constraints.value[0].key);
    expect(_whatIf.example.value).toBeNull();
  });

  it("drops a reply to a list that has changed since", () => {
    const _whatIf = useWhatIf();
    _whatIf.askExample({});
    _whatIf.check({}, { order: 1, expression: "n < 0" });
    calls[1].callback({ verdict: 0 });
    calls[0].callback(EXAMPLE);
    expect(_whatIf.example.value).toBeNull();
  });

  it("says why there is none", () => {
    const _whatIf = useWhatIf();
    _whatIf.askExample({});
    calls[0].callback({ verdict: 1, exampleReason: "Not feasible." });
    expect(_whatIf.example.value).toEqual(
      { loading: false, values: null, reason: "Not feasible." });
  });
});
