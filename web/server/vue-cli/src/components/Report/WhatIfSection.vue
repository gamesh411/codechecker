<template>
  <section class="what-if-section">
    <div ref="scroller" class="what-if-scroller">
      <div class="what-if-content px-3 pt-2 pb-3">
        <h4 class="panel-section-title text-overline">
          What if
        </h4>
        <!-- Once there is a list, it says the same and needs the room. -->
        <p
          v-if="!constraints.length"
          class="panel-section-hint text-caption text-medium-emphasis"
        >
          Assumptions here and at other steps are checked together against
          this path.
        </p>

        <form class="what-if-form d-flex align-start" @submit.prevent="submit">
          <div ref="field" class="what-if-field">
            <v-text-field
              v-model="expression"
              class="what-if-input"
              density="compact"
              variant="outlined"
              hide-details
              :error="!!syntaxError"
              :placeholder="placeholder"
              autocomplete="off"
              spellcheck="false"
              aria-label="What-If Constraint at this step"
              aria-autocomplete="list"
              :aria-expanded="suggestionsOpen ? 'true' : 'false'"
              :aria-controls="listboxId"
              :aria-activedescendant="suggestionsOpen
                ? `${listboxId}-${activeSuggestion}` : undefined"
              :aria-describedby="syntaxError ? errorId : undefined"
              @keydown="onKeydown"
              @focus="onFocus"
              @blur="onBlur"
              @click="updateCaret"
              @keyup="updateCaret"
              @select="updateCaret"
            />
          </div>
          <v-btn
            class="what-if-check"
            type="submit"
            color="primary"
            variant="flat"
            :loading="checking"
            :disabled="!expression.trim() && !constraints.length"
          >
            Check
          </v-btn>
        </form>

        <v-checkbox
          v-model="holdsThroughout"
          class="what-if-throughout"
          density="compact"
          hide-details
          color="primary"
          :title="THROUGHOUT_HINT"
        >
          <template #label>
            <span class="what-if-throughout-label">
              Holds throughout the path
            </span>
          </template>
        </v-checkbox>

        <p
          v-if="syntaxError"
          :id="errorId"
          class="what-if-syntax-error mt-1 mb-0"
          role="alert"
        >
          <code-text :text="syntaxError.message" />
        </p>

        <div v-if="constraints.length" class="what-if-list mt-2">
          <h5 class="panel-group-title text-caption">
            Checked together
          </h5>
          <ul>
            <li
              v-for="constraint in constraints"
              :key="constraint.key"
              class="what-if-row"
              :class="{
                'what-if-row-here': !constraint.holdsThroughout &&
                  constraint.order === step.order,
                'what-if-row-unknown': verdict?.aboutKey === constraint.key
              }"
            >
              <span
                class="what-if-row-step"
                :title="constraint.holdsThroughout
                  ? `Typed at ${constraint.heading}` : undefined"
              >
                {{ constraint.holdsThroughout
                  ? "Throughout" : constraint.heading }}
              </span>
              <code class="what-if-row-expression">
                <name-text :text="constraint.expression" />
              </code>
              <v-btn
                class="what-if-row-remove"
                icon="mdi-close"
                variant="text"
                size="x-small"
                density="comfortable"
                :aria-label="`Remove ${constraint.expression} ` +
                  (constraint.holdsThroughout
                    ? 'throughout' : `at ${constraint.heading}`)"
                title="Remove"
                @click="whatIf.remove(constraint.key)"
              />
            </li>
          </ul>
        </div>

        <div
          v-if="baseline || verdict"
          class="what-if-result mt-2"
          role="status"
        >
          <div v-if="baseline" class="what-if-verdict what-if-baseline">
            <div class="what-if-verdict-label">
              {{ baseline.label }}
            </div>
            <div class="what-if-verdict-text">
              <code-text :text="baseline.text" />
            </div>
          </div>
          <div
            v-if="verdict"
            class="what-if-verdict"
            :class="`what-if-${verdict.kind}`"
          >
            <div class="what-if-verdict-label">
              {{ verdict.label }}
            </div>
            <div class="what-if-verdict-text">
              {{ verdict.lead }}
              <template v-if="verdict.about">
                <code class="what-if-expression"><name-text
                  :text="verdict.about.expression"
                /></code>
                <template v-if="verdict.dead?.atAnchor">
                  at
                  <button
                    type="button"
                    class="what-if-dead-step"
                    :aria-label="`Select ${verdict.dead.label}`"
                    @click="emit('select-step', verdict.dead.order)"
                    v-text="verdict.dead.label"
                  />.
                </template>
                {{ verdict.dead?.atAnchor ? ""
                  : verdict.about.holdsThroughout
                    ? "throughout." : `at ${verdict.about.heading}.` }}
              </template>
              <template v-else>
                these {{ verdict.count }} assumptions together.
              </template>
              <template v-if="verdict.dead?.earliest && verdict.dead.atAnchor">
                It dies right there, the first step whose path condition
                contradicts it.
              </template>
              <template v-else-if="verdict.dead?.earliest">
                <!-- Literal text at both ends keeps the spaces around it. -->
                It dies {{ verdict.dead.beforeAnchor ? "earlier, at" : "at" }}
                <button
                  type="button"
                  class="what-if-dead-step"
                  :aria-label="`Select ${verdict.dead.label}`"
                  @click="emit('select-step', verdict.dead.order)"
                  v-text="verdict.dead.label"
                />,
                {{ verdict.dead.beforeAnchor
                  ? "whose path condition already contradicts"
                  : "the first step whose path condition contradicts" }}
                {{ verdict.count > 1 ? "them" : "it" }}.
              </template>
              <template v-else-if="verdict.dead">
                It is dead by
                <button
                  type="button"
                  class="what-if-dead-step"
                  :aria-label="`Select ${verdict.dead.label}`"
                  @click="emit('select-step', verdict.dead.order)"
                  v-text="verdict.dead.label"
                />. The solver gave up before it could tell whether an
                earlier step is dead too.
              </template>
              <!-- The space would be dropped between two tags. -->
              <template v-if="verdict.trail">
                {{ " " }}<code-text :text="verdict.trail" />
              </template>
            </div>
          </div>
        </div>
      </div>
    </div>
    <div v-if="canScrollUp" class="scroll-shadow scroll-shadow-top" />
    <div v-if="canScrollDown" class="scroll-shadow scroll-shadow-bottom" />

    <teleport to="body">
      <ul
        v-if="suggestionsOpen"
        :id="listboxId"
        class="what-if-suggestions"
        role="listbox"
        aria-label="Names at this step"
        :style="suggestionsStyle"
      >
        <li
          v-for="(suggestion, index) in suggestions"
          :id="`${listboxId}-${index}`"
          :key="suggestion.name"
          class="what-if-suggestion"
          :class="{ 'what-if-suggestion-active': index === activeSuggestion }"
          role="option"
          :aria-selected="index === activeSuggestion ? 'true' : 'false'"
          @mousedown.prevent="accept(index)"
          @mousemove="activeSuggestion = index"
        >
          <span class="what-if-suggestion-name">
            <name-text :text="suggestion.name" />
          </span>
          <span
            class="what-if-suggestion-value"
            :class="{ 'what-if-intervals': suggestion.value.intervals }"
            :title="suggestion.value.text"
          >
            <template v-if="suggestion.value.intervals">
              <template
                v-for="(interval, at) in suggestion.value.intervals"
                :key="at"
              >
                {{ at ? ", " : "" }}<span class="what-if-interval">{{
                  interval }}</span>
              </template>
            </template>
            <template v-else>
              {{ suggestion.value.text }}
            </template>
            <span
              v-if="suggestion.value.dropped"
              class="what-if-suggestion-dropped"
              title="The analyzer no longer tracks this value at this step"
            >dropped</span>
          </span>
        </li>
      </ul>
    </teleport>
  </section>
</template>

<script setup>
import {
  computed,
  h,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  useId,
  watch
} from "vue";

import { PathVerdict } from "@cc/report-server-types";

import { useScrollShadows } from "@/composables/useScrollShadows";

import { compareNames, readableRange } from "./ConstraintTrace";

const props = defineProps({
  step: { type: Object, required: true },
  reportId: { type: Object, required: true },
  // From useWhatIf: owned by the report view, so it outlives this panel.
  whatIf: { type: Object, required: true },
  // The Path Step the reader sees for an `order`: { label, order }.
  resolveStep: { type: Function, required: true }
});

const emit = defineEmits([ "select-step" ]);

const THROUGHOUT_HINT = "Apply it to every value the variable takes on " +
  "this path, also after a call or an assignment replaces it.";

// The report view keeps one useWhatIf for its lifetime, so the refs stay
// those of the prop.
// eslint-disable-next-line vue/no-setup-props-reactivity-loss
const { constraints, result, checking } = props.whatIf;

// A long access path or function name wraps after a separator, never inside
// an identifier or inside `->`.
function NameText({ text }) {
  return (text ?? "").split(/(?<=\.|->|::|\[)/).flatMap((part, index) =>
    index ? [ h("wbr"), part ] : [ part ]);
}
NameText.props = [ "text" ];

// Server messages quote names and operators in backticks.
function CodeText({ text }) {
  return (text ?? "").split("`").map((part, index) => index % 2
    ? h("code", { class: "what-if-code" }, h(NameText, { text: part }))
    : part);
}
CodeText.props = [ "text" ];

const expression = ref("");
const holdsThroughout = ref(false);
const syntaxError = ref(null);
const field = ref(null);
const scroller = ref(null);

const { canScrollUp, canScrollDown } = useScrollShadows(scroller);

const listboxId = `what-if-suggestions-${useId()}`;
const errorId = `what-if-error-${useId()}`;

// The names the server can check a constraint on at this step, as it
// resolves them, which a trace without solver input has none of.
const names = computed(() =>
  props.step.whatIfNames?.(holdsThroughout.value) ?? []);

// Suggests a name the check can decide: a variable of the step's own function
// that holds a symbol, then one that holds an integer, as before callers and
// memory had names, and else the shortest name, which fits a narrow field.
const placeholder = computed(() => {
  const _anchored = new Set((props.step.whatIfNames?.(false) ?? [])
    .map(name => name.name));
  const _own = props.step.variables.filter(v => _anchored.has(v.name));
  const _name = _own.find(v => v.kind === "symbol")?.name ??
    _own.find(v => v.kind === "integer")?.name ??
    [ ..._anchored ].sort((a, b) => a.length - b.length)[0];
  return _name ? `${_name} == 0` : "";
});

// The text the error was about is gone once the reader edits it.
watch(expression, () => {
  syntaxError.value = null;
  suggestionsDismissed.value = false;
  suggestionsForced.value = false;
});

function inputElement() {
  return field.value?.querySelector("input");
}

// A `role` given to v-text-field also lands on its outer field, which would
// announce two comboboxes.
onMounted(() => inputElement()?.setAttribute("role", "combobox"));

async function submit() {
  if (suggestionsOpen.value) {
    accept(activeSuggestion.value);
    return;
  }
  if (checking.value) return;

  const _text = expression.value.trim();
  if (!_text && !constraints.value.length) return;

  const _error = await props.whatIf.check(props.reportId, _text ? {
    order: props.step.order,
    heading: props.step.heading,
    expression: _text,
    holdsThroughout: holdsThroughout.value
  } : null);

  if (!_error) {
    if (_text && expression.value.trim() === _text) expression.value = "";
    return;
  }

  // Leading spaces were trimmed before sending, so the offset is moved back
  // onto the reader's own text.
  const _offset = _error.offset + expression.value.indexOf(_text);
  await nextTick();
  syntaxError.value = _error;
  const _input = inputElement();
  _input?.focus();
  _input?.setSelectionRange(_offset, _offset);
  updateCaret();
}

// Every verdict is about this path: the bug may still be reachable through
// another path the analyzer did not report.
const VERDICT_LABELS = {
  [PathVerdict.FEASIBLE]: "Feasible",
  [PathVerdict.INFEASIBLE]: "Infeasible",
  [PathVerdict.UNKNOWN]: "Unknown"
};

const baseline = computed(() => {
  const _response = result.value?.response;
  if (!_response || _response.baselineVerdict === PathVerdict.FEASIBLE) {
    return null;
  }

  const _yours = result.value.constraints.length > 1
    ? "your assumptions" : "your assumption";
  return {
    label: `Without ${_yours}: ${VERDICT_LABELS[_response.baselineVerdict]}`,
    text: _response.baselineVerdict === PathVerdict.INFEASIBLE
      ? "Z3 finds this path infeasible even with nothing added, although " +
        `the analyzer's own solver accepted it. It is not ${_yours} that ` +
        "rules it out."
      : "There is no verdict for this path even with nothing added. " +
        _response.baselineReason
  };
});

// The verdict reads `lead`, then the constraint it is about (or how many were
// checked together), then `trail`.
const verdict = computed(() => {
  const _checked = result.value;
  if (!_checked) return null;
  const _response = _checked.response;

  // The same reason twice says nothing new, e.g. a report without solver
  // input, so it is shown once, as the baseline.
  if (baseline.value && _response.verdict === PathVerdict.UNKNOWN &&
      _response.reason === _response.baselineReason) {
    return null;
  }

  const _index = _response.reasonConstraintIndex?.toNumber();
  const _about = _index !== undefined ? _checked.constraints[_index] :
    _checked.constraints.length === 1 ? _checked.constraints[0] : null;
  const _common = {
    label: VERDICT_LABELS[_response.verdict],
    about: _about,
    aboutKey: _index !== undefined ? _about?.key : null,
    count: _checked.constraints.length
  };

  switch (_response.verdict) {
  case PathVerdict.FEASIBLE:
    return { ..._common, kind: "feasible",
      lead: "This path stays feasible with", trail: "" };
  case PathVerdict.INFEASIBLE:
    return { ..._common, kind: "infeasible",
      lead: "This path is infeasible with",
      dead: deadStep(_response, _about, _checked.constraints.length),
      trail: "The bug may still be reachable through another path." };
  default:
    return { ..._common, kind: "unknown",
      lead: "No verdict for this path with", trail: _response.reason };
  }
});

// The server names the earliest dead step; the reader sees it as the step
// that line selection would pick, which can be a later one on the same line.
// When the solver gave up during the search, the step is only proven dead,
// not first. A server older than API 6.79 sends no flag, and its step keeps
// reading as the first, as before.
function deadStep(response, about, count) {
  const _order = response.firstDeadStep?.toNumber();
  const _step = _order !== undefined ? props.resolveStep(_order) : null;
  if (!_step) return null;

  return {
    label: _step.label,
    order: _order,
    earliest: response.firstDeadStepIsEarliest !== false,
    beforeAnchor: count === 1 && !about.holdsThroughout &&
      _step.order < about.order,
    // Naming the step twice in one verdict reads as a stutter.
    atAnchor: count === 1 && !about.holdsThroughout &&
      props.resolveStep(about.order)?.order === _step.order
  };
}

// Name suggestions: the names of the selected step that start with the word
// being typed where a name belongs. An empty input lists them only on
// ArrowDown, so that Enter there still checks.
const caret = ref(0);
const focused = ref(false);
const suggestionsDismissed = ref(false);
const suggestionsForced = ref(false);
const activeSuggestion = ref(0);
const suggestionsStyle = ref({});

function updateCaret() {
  caret.value = inputElement()?.selectionStart ?? expression.value.length;
}

// The characters of a name: an access path such as `p->cells[0].width` or
// `*q`, and a function before `::`. An operator follows a space or a digit
// in practice, so `->` and a subscript are read as part of the name.
const wordAtCaret = computed(() => {
  const _before = expression.value.slice(0, caret.value);
  const _match = _before.match(/(?:^|&&)\s*([A-Za-z_*][\w.:*[\]>-]*)?$/);
  if (!_match) return null;

  const _prefix = _match[1] ?? "";
  const _rest = expression.value.slice(caret.value)
    .match(/^[\w.:*[\]>-]*/)[0];
  return {
    prefix: _prefix,
    word: _prefix + _rest,
    start: caret.value - _prefix.length,
    end: caret.value + _rest.length
  };
});

const knownRanges = computed(() =>
  new Map(props.step.symbolRanges.map(range => [ range.symbol, range.range ]))
);
const pathRanges = computed(() =>
  new Map(props.step.pathCondition.map(range => [ range.symbol, range.range ]))
);

// What a name stands for, as the panel reads it: { text, dropped }, a
// concrete value, or the range of its symbol, the last one the path condition
// has when the analyzer dropped it here.
function valueOf(values) {
  if (values.length !== 1) return { text: `${values.length} values` };
  const [ _value ] = values;
  if (_value.kind !== "symbol") return { text: _value.value };
  const _known = knownRanges.value.get(_value.value);
  const _range = _known ?? pathRanges.value.get(_value.value);
  if (!_range) return { text: "no known range" };
  const _readable = readableRange(_range, _value.value,
    props.step.typeOf?.(_value.value));
  return {
    text: _readable ?? _range.replace(/^\{\s*|\s*\}$/g, ""),
    // A range of several intervals wraps between them only.
    intervals: _readable ? null : _range.match(/\[[^\]]*\]/g),
    dropped: !_known
  };
}

const suggestions = computed(() => {
  const _word = wordAtCaret.value;
  if (!_word) return [];
  if (!expression.value.trim() && !suggestionsForced.value) return [];

  const _matches = names.value.filter(name =>
    startsAPart(name.name, _word.prefix));
  // A value that has a name of its own is not offered again under its
  // function's name, unless that is what the reader is typing.
  const _values = name => name.values.map(value =>
    `${value.kind}:${value.value}`).join(" ");
  const _plain = new Set(_matches.filter(name => !name.name.includes("::"))
    .map(_values));
  const _shown = _matches
    .filter(name => !name.name.includes("::") || !_plain.has(_values(name)))
    .sort((a, b) => compareNames(a.name, b.name))
    .map(name => ({ name: name.name, value: valueOf(name.values) }));
  // A finished name needs no suggestion.
  return _shown.length === 1 && _shown[0].name === _word.word
    ? [] : _shown;
});

const suggestionsOpen = computed(() =>
  focused.value && !suggestionsDismissed.value && !checking.value &&
  suggestions.value.length > 0
);

watch(suggestions, () => { activeSuggestion.value = 0; });

// Whether a name, or a part of it after `::`, `->` or `.`, starts with the
// text typed, so that `n` finds `top::n` when `n` alone is ambiguous, and
// `SimpleTy` finds `Op.Node->ValueList[Op.ResNo].V.SimpleTy`.
function startsAPart(name, prefix) {
  return [ 0, ...[ ...name.matchAll(/::|->|\./g) ]
    .map(match => match.index + match[0].length) ]
    .some(at => name.startsWith(prefix, at));
}

function accept(index) {
  const _word = wordAtCaret.value;
  const _suggestion = suggestions.value[index];
  if (!_word || !_suggestion) return;

  const _after = expression.value.slice(_word.end);
  const _space = _after.startsWith(" ") ? "" : " ";
  expression.value =
    expression.value.slice(0, _word.start) + _suggestion.name + _space +
    _after;

  const _caret = _word.start + _suggestion.name.length + 1;
  nextTick(() => {
    inputElement()?.setSelectionRange(_caret, _caret);
    updateCaret();
    // Right after a name comes an operator, not another name.
    suggestionsDismissed.value = true;
  });
}

function onKeydown(event) {
  if (!suggestionsOpen.value) {
    if (event.key === "ArrowDown") {
      suggestionsDismissed.value = false;
      suggestionsForced.value = true;
      event.preventDefault();
    }
    return;
  }

  const _count = suggestions.value.length;
  switch (event.key) {
  case "ArrowDown":
    activeSuggestion.value = (activeSuggestion.value + 1) % _count;
    break;
  case "ArrowUp":
    activeSuggestion.value = (activeSuggestion.value + _count - 1) % _count;
    break;
  case "Tab":
  case "Enter":
    accept(activeSuggestion.value);
    break;
  case "Escape":
    suggestionsDismissed.value = true;
    break;
  default:
    return;
  }
  event.preventDefault();
  event.stopPropagation();
}

function onFocus() {
  focused.value = true;
  nextTick(updateCaret);
}

function onBlur() {
  focused.value = false;
  suggestionsForced.value = false;
}

// The list floats over the page, so no scrolling container of the panel clips
// it. It opens upwards when the input sits low in the window. A narrow field
// gets a wider list, which grows to the left to stay in the window, so that a
// long access path and its value both stay readable.
const SUGGESTIONS_MAX_HEIGHT = 200;
const SUGGESTIONS_MIN_WIDTH = 320;
const WINDOW_MARGIN = 8;

function placeSuggestions() {
  const _rect = field.value?.getBoundingClientRect();
  if (!_rect) return;

  const _below = window.innerHeight - _rect.bottom;
  const _opensUp = _below < SUGGESTIONS_MAX_HEIGHT + 8 && _rect.top > _below;
  const _width = Math.min(Math.max(_rect.width, SUGGESTIONS_MIN_WIDTH),
    window.innerWidth - 2 * WINDOW_MARGIN);
  const _left = Math.max(WINDOW_MARGIN, Math.min(_rect.left,
    window.innerWidth - WINDOW_MARGIN - _width));
  suggestionsStyle.value = {
    left: `${_left}px`,
    width: `${_width}px`,
    maxHeight: `${SUGGESTIONS_MAX_HEIGHT}px`,
    ...(_opensUp
      ? { bottom: `${window.innerHeight - _rect.top + 2}px` }
      : { top: `${_rect.bottom + 2}px` })
  };
}

watch(suggestionsOpen, open => {
  if (open) {
    placeSuggestions();
    window.addEventListener("resize", placeSuggestions);
    window.addEventListener("scroll", placeSuggestions, true);
  } else {
    window.removeEventListener("resize", placeSuggestions);
    window.removeEventListener("scroll", placeSuggestions, true);
  }
});

onBeforeUnmount(() => {
  window.removeEventListener("resize", placeSuggestions);
  window.removeEventListener("scroll", placeSuggestions, true);
});
</script>

<style lang="scss">
.what-if-section {
  position: relative;
  display: flex;
  flex-direction: column;
  min-height: 0;
  border-top: 2px solid #c6ccd4;

  .what-if-scroller {
    min-height: 0;
    overflow-y: auto;
  }

  // In a narrow panel the Check button drops below the input instead of
  // squeezing it until a placeholder such as "flags == 0" is cut off.
  .what-if-form {
    flex-wrap: wrap;
    gap: 8px;
  }

  .what-if-field {
    flex: 1 1 10rem;
    min-width: 0;
  }

  // A placeholder on a long access path ends in an ellipsis rather than
  // being cut at the field's edge.
  .what-if-input input {
    font-family: monospace;
    font-size: 0.875rem;
    text-overflow: ellipsis;
  }

  .what-if-check {
    height: 40px;
  }

  // Sits under the input, aligned with its left edge.
  .what-if-throughout {
    margin: 2px 0 0 -6px;

    .v-selection-control {
      min-height: 30px;
    }

    .v-selection-control__wrapper {
      width: 30px;
      height: 30px;
    }
  }

  .what-if-throughout-label {
    color: rgba(0, 0, 0, 0.75);
    font-size: 0.8125rem;
  }

  .what-if-dead-step {
    padding: 0;
    color: #1565c0;
    font-weight: 600;
    text-decoration: underline;
    text-underline-offset: 2px;
    cursor: pointer;
  }

  .what-if-dead-step:hover,
  .what-if-dead-step:focus-visible {
    color: #0d47a1;
  }

  .what-if-syntax-error {
    color: #b3261e;
    font-size: 0.8125rem;
    line-height: 1.35;
  }

  // One grid for all rows, so step names of any length keep the
  // expressions in one column.
  .what-if-list ul {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr) auto;
    margin: 0;
    padding: 0;
    list-style: none;
  }

  .what-if-row {
    display: grid;
    grid-column: 1 / -1;
    grid-template-columns: subgrid;
    align-items: center;
    column-gap: 8px;
    padding: 1px 0 1px 6px;
    border-left: 3px solid transparent;
    border-bottom: 1px solid #edf0f3;
    font-size: 0.8125rem;
  }

  .what-if-row:last-child {
    border-bottom: 0;
  }

  .what-if-row-here .what-if-row-step {
    color: rgba(0, 0, 0, 0.87);
    font-weight: 600;
  }

  .what-if-row-unknown {
    border-left-color: #6b7580;
    background-color: #f1f3f5;
  }

  .what-if-row-step {
    color: rgba(0, 0, 0, 0.6);
    font-size: 0.75rem;
    white-space: nowrap;
  }

  .what-if-row-expression {
    font-size: 0.8125rem;
    overflow-wrap: anywhere;
  }

  .what-if-verdict {
    padding: 6px 10px;
    border-left: 4px solid #9aa3ad;
    border-radius: 3px;
    background-color: #f1f3f5;
    font-size: 0.8125rem;
    line-height: 1.4;
  }

  .what-if-verdict + .what-if-verdict {
    margin-top: 6px;
  }

  .what-if-verdict-label {
    font-weight: 600;
  }

  .what-if-code {
    padding: 0 2px;
    border-radius: 2px;
    background-color: rgba(0, 0, 0, 0.06);
    font-size: 0.8125rem;
  }

  // A short expression moves to the next line whole instead of breaking at
  // its spaces; one longer than the line still wraps inside its box.
  .what-if-expression {
    display: inline-block;
    max-width: 100%;
    padding: 0 3px;
    border-radius: 2px;
    background-color: rgba(0, 0, 0, 0.06);
    font-size: 0.8125rem;
    line-height: 1.35;
    overflow-wrap: anywhere;
  }

  .what-if-feasible {
    border-left-color: #2e7d32;
    background-color: #edf6ee;
  }

  .what-if-infeasible {
    border-left-color: #c62828;
    background-color: #fbeeee;
  }

  .what-if-baseline {
    border-left-color: #b26a00;
    background-color: #fdf4e3;
  }
}

.what-if-suggestions {
  position: fixed;
  z-index: 2400;
  margin: 0;
  padding: 4px 0;
  overflow-y: auto;
  list-style: none;
  border: 1px solid #c6ccd4;
  border-radius: 4px;
  background-color: #fff;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
  font-size: 0.8125rem;

  .what-if-suggestion {
    display: flex;
    align-items: baseline;
    column-gap: 12px;
    padding: 3px 12px;
    cursor: pointer;
  }

  .what-if-suggestion-active {
    background-color: #e8eef6;
  }

  // A long name wraps at its separators and keeps the value beside it; an
  // identifier longer than the line still wraps inside it.
  .what-if-suggestion-name {
    flex: 1 1 auto;
    min-width: 0;
    font-family: monospace;
    font-weight: 600;
    overflow-wrap: anywhere;
  }

  // A value stays on one line, except a range of several intervals, which
  // wraps between them rather than being cut.
  .what-if-suggestion-value {
    flex: 0 0 auto;
    max-width: 55%;
    margin-left: auto;
    color: rgba(0, 0, 0, 0.55);
    font-family: monospace;
    font-size: 0.75rem;
    text-align: right;
    // Only a concrete value such as a long address is ever this wide.
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .what-if-intervals {
    flex-shrink: 1;
    white-space: normal;
  }

  .what-if-interval {
    white-space: nowrap;
  }

  .what-if-suggestion-dropped {
    margin-left: 4px;
    padding: 0 4px;
    border-radius: 3px;
    color: #6b5a12;
    background-color: #f6efc8;
    font-family: sans-serif;
    white-space: nowrap;
  }
}
</style>
