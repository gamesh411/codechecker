import { ref } from "vue";

import { ccService, handleThriftError } from "@cc-api";
import { WhatIfConstraint } from "@cc/report-server-types";

// The What-If Constraints of the open report view, checked together. They are
// never stored, so opening another report starts with none.
export function useWhatIf() {
  // Each { key, order, heading, expression, holdsThroughout }: `order`
  // anchors it to a Path Step, `heading` names that step for the reader.
  // A constraint that holds throughout is about every value its variables
  // take on the path, so its anchor is only where it was typed.
  const constraints = ref([]);
  // The last check: { constraints, response }, where `constraints` is the
  // list that was checked.
  const result = ref(null);
  const checking = ref(false);
  // One example of the path under the listed constraints, asked for apart
  // from a check: { loading, values, reason }, where `values` maps a symbol
  // of the requires list to the decimal the server gave it, and `reason` says
  // why there are none. It matches the list it was made for only, so any
  // change to the list drops it. It is never stored.
  const example = ref(null);

  let _nextKey = 0;
  // A reply to a request made before the list last changed is dropped.
  let _generation = 0;
  // Counts changes to the list alone, which the example depends on; a check
  // that adds nothing leaves it.
  let _listVersion = 0;

  function _listChanged() {
    _listVersion += 1;
    example.value = null;
  }

  function _changed() {
    _generation += 1;
    result.value = null;
    checking.value = false;
    _listChanged();
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
  // expression, holdsThroughout }) when given. `added` joins the list only
  // if it can be read; otherwise the promise resolves to its syntax error and
  // nothing changes.
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
          expression: c.expression,
          holdsThroughout: !!c.holdsThroughout
        })),
        false,
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

          if (added) _listChanged();
          constraints.value = _candidates;
          result.value = { constraints: _candidates, response };
          resolve(null);
        }, () => {
          if (_generationAtStart === _generation) checking.value = false;
          resolve(null);
        }));
    });
  }

  // Asks for one example of the path under the listed constraints. The
  // verdict that comes with it is the list's, which `result` already shows
  // once the list was checked, so it is not shown again.
  function askExample(reportId) {
    const _version = _listVersion;
    example.value = { loading: true, values: null, reason: null };

    ccService.getClient().checkWhatIf(
      reportId,
      constraints.value.map(c => new WhatIfConstraint({
        pathStep: c.order,
        expression: c.expression,
        holdsThroughout: !!c.holdsThroughout
      })),
      true,
      handleThriftError(response => {
        if (_version !== _listVersion) return;
        example.value = {
          loading: false,
          values: response.example ? new Map(response.example.map(value =>
            [ value.symbol, value.value ])) : null,
          reason: response.example ? null : response.exampleReason ??
            "The server gave no example."
        };
      }, () => {
        if (_version === _listVersion) example.value = null;
      }));
  }

  function hideExample() {
    example.value = null;
  }

  return {
    constraints, result, checking, example, check, remove, reset,
    askExample, hideExample
  };
}
