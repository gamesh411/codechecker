import { createSSRApp, ref } from "vue";
import { renderToString } from "vue/server-renderer";

import WhatIfSection from "@/components/Report/WhatIfSection";

// The generated API is not built for the unit tests; only the verdict
// numbers are read from it.
jest.mock("@cc/report-server-types", () => ({
  PathVerdict: { FEASIBLE: 0, INFEASIBLE: 1, UNKNOWN: 2 }
}), { virtual: true });

global.ResizeObserver = class {
  observe() {}
  disconnect() {}
};

// The text of the What-If section after a check, as the page renders it.
async function rendered(response) {
  const constraint = {
    key: 0, order: 10, heading: "Step 10", expression: "n == 6",
    holdsThroughout: false
  };
  const app = createSSRApp(WhatIfSection, {
    step: { order: 10, heading: "Step 10", variables: [] },
    reportId: {},
    resolveStep: order => ({ order, label: `Step ${order}` }),
    whatIf: {
      constraints: ref([ constraint ]),
      result: ref({ constraints: [ constraint ], response }),
      checking: ref(false)
    }
  });
  [ "v-btn", "v-checkbox", "v-text-field" ].forEach(name =>
    app.component(name, { template: "<div><slot /></div>" }));
  return (await renderToString(app)).replace(/<!--.*?-->/g, "");
}

describe("the What-If verdict", () => {
  test("an Unknown reason is spaced from the step before it", async () => {
    const html = await rendered({
      verdict: 2, baselineVerdict: 0,
      reason: "`n` names 2 values at this Path Step."
    });
    // The reason opens with a code span, and a template drops the space
    // between a text and a tag it does not spell out.
    expect(html).toContain("at Step 10. <code");
    expect(html).toContain("</code> names 2 values");
  });
});
