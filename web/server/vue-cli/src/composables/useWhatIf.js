import { ref } from "vue";

import { ccService, handleThriftError } from "@cc-api";
import { WhatIfConstraint } from "@cc/report-server-types";

// The What-If Constraints of the open report view, checked together. They are
// never stored, so opening another report starts with none.
export function useWhatIf() {
  // Each { key, order, heading, expression }: `order` anchors it to a Path
  // Step, `heading` names that step for the reader.
  const constraints = ref([]);
  // The last check: { constraints, response }, where `constraints` is the
  // list that was checked.
  const result = ref(null);
  const checking = ref(false);

  let _nextKey = 0;
  // A reply to a request made before the list last changed is dropped.
  let _generation = 0;

  function _changed() {
    _generation += 1;
    result.value = null;
    checking.value = false;
  }

  function reset() {
    constraints.value = [];
    _changed();
  }

  function remove(key) {
    constraints.value = constraints.value.filter(c => c.key !== key);
    _changed();
  }

  // Checks the listed constraints, plus `added` ({ order, heading,
  // expression }) when given. `added` joins the list only if it can be read;
  // otherwise the promise resolves to its syntax error and nothing changes.
  function check(reportId, added) {
    const _candidates = added
      ? [ ...constraints.value, { ...added, key: _nextKey++ } ]
      : constraints.value;
    const _generationAtStart = ++_generation;
    checking.value = true;

    return new Promise(resolve => {
      ccService.getClient().checkWhatIf(
        reportId,
        _candidates.map(c => new WhatIfConstraint({
          pathStep: c.order,
          expression: c.expression
        })),
        handleThriftError(response => {
          if (_generationAtStart !== _generation) return resolve(null);
          checking.value = false;

          const _error = response.syntaxError;
          if (added && _error &&
              _error.constraintIndex.toNumber() === _candidates.length - 1) {
            return resolve({
              message: _error.message,
              offset: _error.offset.toNumber()
            });
          }

          constraints.value = _candidates;
          result.value = { constraints: _candidates, response };
          resolve(null);
        }, () => {
          if (_generationAtStart === _generation) checking.value = false;
          resolve(null);
        }));
    });
  }

  return { constraints, result, checking, check, remove, reset };
}
