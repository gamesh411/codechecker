<template>
  <v-card
    class="constraint-snapshot-panel d-flex flex-column"
    variant="flat"
    rounded="0"
  >
    <div class="panel-header d-flex align-center px-3 py-2">
      <span class="text-subtitle-2">Constraint Snapshot</span>
      <v-spacer />
      <v-btn
        v-if="canShowGraph && compactHeader"
        class="panel-graph-btn mr-1"
        icon="mdi-graph-outline"
        variant="text"
        size="small"
        density="compact"
        aria-label="Show in the control flow graph"
        title="Show this step in its function's control flow graph"
        @click="emit('open-graph')"
      />
      <v-btn
        v-else-if="canShowGraph"
        class="panel-graph-btn mr-1"
        variant="text"
        size="small"
        density="comfortable"
        prepend-icon="mdi-graph-outline"
        title="Show this step in its function's control flow graph"
        @click="emit('open-graph')"
      >
        CFG
      </v-btn>
      <v-btn
        v-if="canShowStates && compactHeader"
        class="panel-graph-btn mr-1"
        icon="mdi-layers-outline"
        variant="text"
        size="small"
        density="compact"
        aria-label="Show among the analyzer states"
        title="Show this step among the analyzer states of the Bug Path"
        @click="emit('open-states')"
      />
      <v-btn
        v-else-if="canShowStates"
        class="panel-graph-btn mr-1"
        variant="text"
        size="small"
        density="comfortable"
        prepend-icon="mdi-layers-outline"
        title="Show this step among the analyzer states of the Bug Path"
        @click="emit('open-states')"
      >
        States
      </v-btn>
      <v-btn
        class="panel-close-btn"
        icon="mdi-close"
        variant="text"
        size="small"
        density="compact"
        aria-label="Close constraint snapshot"
        title="Close"
        @click="emit('close')"
      />
    </div>

    <v-divider />

    <div class="panel-step px-3 py-2 text-body-2">
      <span class="panel-step-index text-medium-emphasis mr-1">
        {{ step.heading }}
      </span>
      <dump-text :text="step.message" :full-paths="true" />
      <span
        v-if="step.silent"
        class="panel-silent text-caption ml-1"
        :title="SILENT_HINT"
      >
        Assumed, no event
      </span>
      <span
        v-if="diesHere"
        class="panel-dies-here text-caption ml-1"
        :title="diesHere.earliest
          ? 'The first step whose path condition contradicts the What-If ' +
            'Constraints that were checked'
          : 'The path condition here contradicts the What-If Constraints ' +
            'that were checked. An earlier step may too.'"
      >
        {{ diesHere.earliest ? "Path dies here" : "Path dead here" }}
      </span>
    </div>

    <v-divider />

    <div class="panel-scroll-area">
      <div ref="body" class="panel-body">
        <div class="panel-body-content">
          <section class="panel-section panel-known px-3 py-2">
            <h4 class="panel-section-title text-overline">
              Known here
            </h4>
            <p class="panel-section-hint text-caption text-medium-emphasis">
              What the analyzer held at this step.
            </p>

            <div v-if="variableSections.length" class="panel-group">
              <h5 class="panel-group-title text-caption">
                Variables
              </h5>
              <div
                v-for="section in variableSections"
                :key="section.key"
                class="panel-frame"
              >
                <div
                  v-if="section.group"
                  class="panel-frame-heading text-caption"
                  :class="{ 'panel-frame-current': !section.caller }"
                  :title="section.group.function"
                >
                  <button
                    v-if="section.caller"
                    type="button"
                    class="panel-frame-toggle"
                    :aria-expanded="section.expanded ? 'true' : 'false'"
                    :title="section.expanded
                      ? 'Hide the variables of this calling function'
                      : 'Show the variables of this calling function'"
                    @click="toggleFrame(section)"
                  >
                    <v-icon
                      class="panel-frame-chevron"
                      size="small"
                      :icon="section.expanded
                        ? 'mdi-chevron-down' : 'mdi-chevron-right'"
                    />
                    <code class="panel-frame-function">
                      <dump-text :text="section.name" />
                    </code>
                  </button>
                  <code v-else class="panel-frame-function">
                    <dump-text :text="section.name" />
                  </code>
                  <span
                    v-if="section.place"
                    class="panel-frame-where text-medium-emphasis"
                  >
                    called at
                    <button
                      v-if="section.callOrder !== null"
                      type="button"
                      class="panel-label-link"
                      title="Select the Path Step nearest to this call"
                      @click="emit('select-step', section.callOrder)"
                    >{{ section.place }}</button>
                    <span v-else>{{ section.place }}</span>
                  </span>
                  <span
                    v-if="section.caller && !section.expanded"
                    class="panel-frame-count text-medium-emphasis"
                  >
                    {{ section.rows.length }}
                    {{ section.rows.length === 1 ? "variable" : "variables" }}
                  </span>
                </div>
                <template v-if="section.expanded">
                  <p
                    v-if="!section.rows.length"
                    class="panel-frame-empty ma-0 text-caption
                      text-medium-emphasis"
                  >
                    No variable of this function is known here.
                  </p>
                  <div
                    v-for="row in section.rows"
                    :key="row.key"
                    class="panel-row"
                    :class="changeClass(row.change)"
                  >
                    <div class="panel-name" :title="row.name">
                      <dump-text :text="row.name" />
                    </div>
                    <div class="panel-value">
                      <span :class="{ 'text-medium-emphasis': row.muted }">
                        <range-text
                          v-if="row.range"
                          :range="row.range"
                          :symbol="row.symbol"
                        />
                        <dump-text v-else :text="row.display" />
                      </span>
                      <change-tag :change="row.change" />
                      <span
                        v-if="row.dropped"
                        class="panel-dropped text-caption"
                        :title="DROPPED_HINT"
                      >
                        dropped
                      </span>
                      <div
                        v-if="row.symbol"
                        class="panel-symbol"
                        :title="row.symbol"
                      >
                        <symbol-text :symbol="row.symbol" :unless="row.name" />
                      </div>
                    </div>
                  </div>
                </template>
              </div>
            </div>

            <div v-if="rows.others.length" class="panel-group">
              <h5 class="panel-group-title text-caption">
                Other symbols
              </h5>
              <div
                v-for="range in rows.others"
                :key="range.key"
                class="panel-row panel-row-raw"
                :class="changeClass(range.change)"
              >
                <div class="panel-name panel-raw-symbol" :title="range.symbol">
                  <symbol-text :symbol="range.symbol" />
                </div>
                <div class="panel-value">
                  <range-text :range="range.range" :symbol="range.symbol" />
                  <change-tag :change="range.change" />
                </div>
              </div>
            </div>

            <p
              v-if="!variableSections.length && !rows.others.length"
              class="panel-empty ma-0 text-body-2 text-medium-emphasis"
            >
              No variables or symbol ranges are known at this step.
            </p>
          </section>

          <section class="panel-section panel-accumulated px-3 py-2">
            <h4 class="panel-section-title text-overline">
              Accumulated path condition
            </h4>
            <p class="panel-section-hint text-caption text-medium-emphasis">
              Everything assumed from the start of the path up to this step,
              including values the analyzer has since dropped.
            </p>

            <div
              v-for="row in rows.accumulated"
              :key="row.key"
              class="panel-row"
              :class="[
                { 'panel-row-raw': !row.name }, changeClass(row.change)
              ]"
            >
              <div
                class="panel-name"
                :class="{ 'panel-raw-symbol': !row.name }"
                :title="row.symbol"
              >
                <dump-text v-if="row.name" :text="row.name" />
                <symbol-text v-else :symbol="row.symbol" />
              </div>
              <div class="panel-value">
                <range-text :range="row.range" :symbol="row.symbol" />
                <change-tag :change="row.change" />
                <div v-if="row.name || row.dropped" class="panel-meta">
                  <span
                    v-if="row.name"
                    class="panel-symbol"
                    :title="row.symbol"
                  >
                    <symbol-text :symbol="row.symbol" :unless="row.name" />
                  </span>
                  <span
                    v-if="row.dropped"
                    class="panel-dropped text-caption"
                    :title="DROPPED_HINT"
                  >
                    dropped
                  </span>
                </div>
              </div>
            </div>

            <p
              v-if="!rows.accumulated.length"
              class="panel-empty ma-0 text-body-2 text-medium-emphasis"
            >
              No constraints have been collected on this path yet.
            </p>
          </section>
        </div>
      </div>
      <div v-if="canScrollUp" class="scroll-shadow scroll-shadow-top" />
      <div v-if="canScrollDown" class="scroll-shadow scroll-shadow-bottom" />
    </div>

    <what-if-section
      class="panel-what-if"
      :step="step"
      :report-id="reportId"
      :what-if="whatIf"
      :resolve-step="resolveStep"
      @select-step="emit('select-step', $event)"
    />
  </v-card>
</template>

<script setup>
import { computed, h, reactive, ref } from "vue";
import { useDisplay } from "vuetify";

import { useScrollShadows } from "@/composables/useScrollShadows";

import {
  functionName, labelPlace, readableRange, shortenPaths
} from "./ConstraintTrace";
import { EXPANDED_CALLERS, snapshotRows } from "./SnapshotRows";
import WhatIfSection from "./WhatIfSection";

const props = defineProps({
  // Besides the lists: `changes` (what the step added or narrowed), `silent`
  // (an assumption the Bug Path has no event for) and `labelOf`, which gives
  // a symbol's label as symbolLabel or expressionLabel does, plus `order`,
  // the Path Step nearest to its origin, `otherFile`, whether that is in
  // another file, and `sameCall`, the other results of the same call as
  // { call, order }; and `typeOf`, a symbol's type and pointer-ness.
  step: { type: Object, required: true },
  reportId: { type: Object, required: true },
  // From useWhatIf, owned by the report view.
  whatIf: { type: Object, required: true },
  // The Path Step the reader sees for an `order`: { label, order }.
  resolveStep: { type: Function, required: true },
  canShowGraph: { type: Boolean, default: false },
  canShowStates: { type: Boolean, default: false }
});

const emit = defineEmits([
  "close", "select-step", "open-graph", "open-states"
]);

// Below 1280px the panel is too narrow for the title and a labelled button.
const { mdAndDown: compactHeader } = useDisplay();

const diesHere = computed(() => {
  const _response = props.whatIf.result.value?.response;
  const _dead = _response?.firstDeadStep?.toNumber();
  if (_dead === undefined ||
      props.resolveStep(_dead)?.order !== props.step.order) return null;
  return { earliest: _response.firstDeadStepIsEarliest !== false };
});

const SILENT_HINT = "The analyzer assumed something here that the Bug Path " +
  "does not describe";
const CHANGE_HINTS = {
  added: "First known at this step",
  narrowed: "Narrower at this step than at the step before"
};
const CHANGE_LABELS = { added: "new", narrowed: "narrowed" };

function changeClass(change) {
  return change ? [ "panel-row-changed", `panel-row-${change}` ] : [];
}

function ChangeTag({ change }) {
  return change ? h("span", {
    class: [ "panel-change", `panel-change-${change}`, "text-caption" ],
    title: CHANGE_HINTS[change]
  }, CHANGE_LABELS[change]) : null;
}
ChangeTag.props = [ "change" ];

const DROPPED_HINT = "The analyzer no longer tracks this value at this step";

const body = ref(null);
const { canScrollUp, canScrollDown } = useScrollShadows(body);

// One span per interval, so a long range set wraps only between intervals.
// A pointer's null and non-null ranges, and an integer range that readableRange
// can say in words, read as words; the title keeps the analyzer's range.
function RangeText({ range, symbol }) {
  const _readable = readableRange(range, symbol, props.step.typeOf?.(symbol));
  if (_readable) return h("span", { title: range }, _readable);

  const _intervals = range.match(/\[[^\]]*\]/g);
  if (!_intervals) return range;
  return h("span", { class: "panel-intervals", title: range },
    _intervals.map(interval =>
      h("span", { class: "panel-interval" }, interval)));
}
RangeText.props = [ "range", "symbol" ];

// A symbol dump or a C++ name may wrap after a separator or an opening
// bracket, never inside an identifier. A parenthesis that opens a group after
// an operator or another parenthesis stays with its contents, so that no line
// ends in "- (".
const BREAK = /(?<=[,{[<.]|[^\s(]\(|::|->)(?=[^\s,.)}\]>])/;

function DumpText({ text, fullPaths }) {
  // The row's title keeps an absolute path whole.
  const _text = fullPaths ? text ?? "" : shortenPaths(text);
  return _text.split(BREAK).flatMap((part, index) =>
    index ? [ h("wbr"), part ] : [ part ]);
}
DumpText.props = [ "text", "fullPaths" ];

const SAME_CALL_HINT = "The analyzer does not assume that two calls return " +
  "the same value, so it treats these as unrelated values.";

// The small tag on a call result that the analyzer also made for the same call
// text elsewhere, with the Path Step of the nearest other one, which it links
// to. With several others they are all in the title.
function sameCallTag(others) {
  const _step = order => order === null
    ? "a place with no Path Step"
    : props.resolveStep(order)?.label ?? `Step ${order}`;
  const [ _first ] = others;
  const _title = others.length === 1
    ? `${SAME_CALL_HINT} The other is at ${_step(_first.order)}.`
    : `${SAME_CALL_HINT} The others are at ` +
      `${others.map(other => _step(other.order)).join(", ")}.`;
  const _children = [ "another ", h("code", { class: "panel-label-code" },
    h(DumpText, { text: _first.call })), " result" ];
  if (_first.order !== null) {
    _children.push(", ", h("button", {
      type: "button",
      class: "panel-label-link",
      title: "Select the Path Step of the other one",
      onClick: () => emit("select-step", _first.order)
    }, _step(_first.order)));
  }
  if (others.length > 1) _children.push(` +${others.length - 1}`);
  return h("span", { class: "panel-same-call text-caption", title: _title },
    _children);
}

// A symbol as its label when the trace has one, and as its dump otherwise.
// The label's own title gives the whole label, which a clamped line may cut,
// what it means and the dump, which the row's title also keeps. The label's
// line selects the Path Step nearest to the statement or loop the value came
// from. A label that only repeats `unless`, such as a parameter's name beside
// the parameter, or `top::n` beside top's `n`, shows the dump.
function SymbolText({ symbol, unless }) {
  const _label = props.step.labelOf?.(symbol);
  if (!_label || unless !== undefined && (_label.text === unless ||
      unless.endsWith(`::${_label.text}`))) {
    return h(DumpText, { text: symbol });
  }

  const _parts = _label.parts.map(part => typeof part === "string"
    ? part
    : h("code", { class: "panel-label-code" }, h(DumpText, {
      text: part.code
    })));
  const _where = labelPlace(_label);
  if (_where) {
    _parts.push(", ", _label.order === null
      ? h("span", { class: "panel-label-line" }, _where)
      : h("button", {
        type: "button",
        class: "panel-label-line panel-label-link",
        title: "Select the Path Step nearest to where this value came from",
        onClick: () => emit("select-step", _label.order)
      }, _where));
  }
  if (_label.sameCall?.length) _parts.push(" ", sameCallTag(_label.sameCall));
  if (_label.function) {
    // "in" stays with the name, which may still break inside.
    _parts.push(" in\u00a0", h("code", { class: "panel-label-code" },
      h(DumpText, { text: functionName(_label.function) })));
  }
  return h("span", {
    class: "panel-label",
    title: [ _label.text, _label.hint, symbol ].filter(Boolean).join("\n")
  }, _parts);
}
SymbolText.props = [ "symbol", "unless" ];

const rows = computed(() => snapshotRows(props.step));

// Callers a reader opened or closed, by frame, which outlives the step: the
// next step in the same call shows them the same way.
const frameOpen = reactive(new Map());

// The groups of the Variables section. Without callers it is one group with
// no heading, as before there were any. With them, the step's own frame comes
// first and stays open, and each caller follows, open unless there are more
// than a few or the reader closed it.
const variableSections = computed(() => {
  const { variables, current, callers } = rows.value;
  if (!callers.length) {
    return variables.length
      ? [ { key: "own", rows: variables, expanded: true } ] : [];
  }
  const _place = group => group.callSite && labelPlace({
    location: group.callSite,
    otherFile: group.callSite.file !== props.step.file
  });
  const _section = (group, sectionRows, caller) => ({
    key: group.key,
    group,
    // Most of a long C++ name is the anonymous namespace it is in; the
    // heading's title keeps it.
    name: functionName(group.function),
    rows: sectionRows,
    caller,
    expanded: !caller ||
      (frameOpen.get(group.frame) ?? callers.length <= EXPANDED_CALLERS),
    place: _place(group),
    callOrder: group.callSite
      ? props.step.callSiteOrder?.(group.frame) ?? null : null
  });
  return [
    _section(current ?? { key: "own", function: "function" }, variables,
      false),
    ...callers.map(group => _section(group, group.rows, true))
  ];
});

function toggleFrame(section) {
  frameOpen.set(section.group.frame, !section.expanded);
}
</script>

<style lang="scss">
.constraint-snapshot-panel {
  border-top: 1px solid #d8dbe0;
  border-right: 1px solid #d8dbe0;
  border-bottom: 1px solid #d8dbe0;

  .panel-header {
    background-color: #f7f7f7;
  }

  .panel-step {
    overflow-wrap: break-word;
  }

  .panel-step-index {
    font-weight: 500;
  }

  .panel-dies-here {
    display: inline-block;
    padding: 0 6px;
    border-radius: 3px;
    color: #8e1c1c;
    background-color: #fbeeee;
    font-weight: 600;
    line-height: 1.5;
    white-space: nowrap;
  }

  .panel-body {
    flex: 1 1 0;
    min-height: 0;
    overflow-y: auto;
  }

  .panel-body-content {
    display: flex;
    flex-direction: column;
    min-height: 100%;
  }

  .panel-accumulated {
    flex-grow: 1;
  }

  .panel-section-title {
    line-height: 1.5;
  }

  .panel-section-hint {
    margin-bottom: 8px;
    line-height: 1.35;
  }

  .panel-group + .panel-group {
    margin-top: 8px;
  }

  .panel-group-title {
    margin-bottom: 2px;
    font-weight: 500;
    color: rgba(0, 0, 0, 0.6);
  }

  // A frame's heading is a caption of the rows under it. A long C++ name
  // wraps inside it rather than past the panel, and the call site keeps its
  // line whole.
  .panel-frame + .panel-frame {
    margin-top: 6px;
  }

  .panel-frame-heading {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    column-gap: 6px;
    padding: 2px 0;
    border-bottom: 1px solid #e3e7ec;
    color: rgba(0, 0, 0, 0.6);
    line-height: 1.4;
  }

  // The own frame has no chevron, so its name lines up with the callers'.
  .panel-frame-current {
    padding-left: 20px;
  }

  .panel-frame-toggle {
    display: inline-flex;
    align-items: baseline;
    min-width: 0;
    max-width: 100%;
    padding: 0;
    color: inherit;
    text-align: left;
    cursor: pointer;

    &:focus-visible {
      outline: 2px solid #1f5fae;
      outline-offset: 1px;
    }
  }

  // On the first line of a name that wraps.
  .panel-frame-chevron {
    flex: 0 0 auto;
    align-self: flex-start;
    margin-top: 1px;
  }

  .panel-frame-function {
    min-width: 0;
    padding: 0;
    background: none;
    color: rgba(0, 0, 0, 0.87);
    font-family: monospace;
    font-size: 0.8125rem;
    font-weight: 600;
    overflow-wrap: anywhere;
  }

  // A long file name in "called at" wraps instead of running past the panel.
  .panel-frame-where {
    min-width: 0;
    max-width: 100%;
    overflow-wrap: anywhere;
  }

  .panel-frame-count {
    white-space: nowrap;
  }

  .panel-frame-count {
    margin-left: auto;
  }

  .panel-frame-empty {
    padding: 3px 0 3px 20px;
    line-height: 1.35;
  }

  .panel-accumulated {
    border-top: 2px solid #c6ccd4;
    background-color: #f4f6f9;
  }

  // A long path shows hundreds of rows, and laying them all out took a third
  // of a step change at 1205 steps. Rows out of view are skipped until
  // scrolled to; the sizes are a row's usual content height, kept exact once
  // seen.
  //
  // The value keeps the right 74% of the row, so values line up. A name wider
  // than the rest takes a line of its own above its value, rather than
  // breaking inside it, as NumIntermediates did in a 26% column.
  .panel-row {
    display: flex;
    flex-wrap: wrap;
    column-gap: 8px;
    padding: 3px 0;
    font-size: 0.8125rem;
    border-bottom: 1px solid #edf0f3;
    content-visibility: auto;
    contain-intrinsic-size: auto 38px;

    > .panel-name {
      flex: 0 1 auto;
      min-width: 0;
      max-width: 100%;
    }

    > .panel-value {
      flex: 0 0 calc(74% - 8px);
      min-width: 0;
      margin-left: auto;
    }
  }

  // A symbol dump takes the row beside its range, and a range too wide for
  // what is left, such as two 64-bit intervals, goes below it on the right.
  .panel-row-raw {
    contain-intrinsic-size: auto 17px;

    > .panel-name {
      flex: 1 1 55%;
    }

    > .panel-value {
      flex: 0 1 auto;
      max-width: 100%;
      text-align: right;

      .panel-intervals,
      .panel-meta {
        justify-content: flex-end;
      }
    }
  }

  .panel-accumulated .panel-row {
    border-bottom-color: #dfe3e9;
  }

  .panel-row:last-child {
    border-bottom: 0;
  }

  // Lines break where DumpText allows, and inside a word only when the word
  // alone is wider than the line.
  .panel-name,
  .panel-value,
  .panel-symbol {
    font-family: monospace;
    overflow-wrap: break-word;
  }

  .panel-name {
    font-weight: 600;
  }

  // Lighter than its range, so that a range beside a wrapped dump does not
  // read as part of it.
  .panel-name.panel-raw-symbol {
    font-weight: 400;
    color: rgba(0, 0, 0, 0.68);
  }

  .panel-intervals {
    display: flex;
    flex-wrap: wrap;
    column-gap: 0.5rem;
  }

  // Each interval is a flex item, so a set wraps between intervals, and an
  // interval breaks inside only when it alone is wider than the column.
  .panel-interval {
    min-width: 0;
    overflow-wrap: break-word;
  }

  // Beside a name the symbol is secondary, and a field's symbol on a real
  // report runs to several lines, so it shows two and keeps the rest in its
  // title.
  .panel-symbol {
    color: rgba(0, 0, 0, 0.55);
    font-size: 0.75rem;
    display: -webkit-box;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 2;
    overflow: hidden;
  }

  .panel-meta {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    column-gap: 8px;
  }

  // A label is prose with code in it, so it reads apart from the dumps and
  // names around it.
  .panel-label {
    font-family: sans-serif;
    font-weight: 400;
  }

  .panel-label-code {
    font-family: monospace;
    font-size: inherit;
    background: none;
    padding: 0;
  }

  .panel-label-line {
    white-space: nowrap;
  }

  .panel-label-link {
    color: #1f5fae;
    text-decoration: underline dotted;
    text-underline-offset: 2px;
    cursor: pointer;

    &:hover,
    &:focus-visible {
      text-decoration-style: solid;
    }
  }

  // Quiet beside the label, so that it does not read as part of a range or
  // compete with the change tags.
  .panel-same-call {
    display: inline-block;
    padding: 0 5px;
    border-radius: 3px;
    color: rgba(0, 0, 0, 0.6);
    background-color: #eceff3;
    font-family: sans-serif;
    line-height: 1.5;
    max-width: 100%;
    // A long call or step name wraps inside the tag rather than past the row.
    overflow-wrap: break-word;
    cursor: help;
  }

  .panel-dropped {
    padding: 0 5px;
    border-radius: 3px;
    color: #6b5a12;
    background-color: #f6efc8;
    white-space: nowrap;
  }

  // Beside a variable's range, as the change tag sits.
  .panel-value > .panel-dropped {
    display: inline-block;
    margin-left: 6px;
    line-height: 1.5;
  }

  .panel-silent {
    display: inline-block;
    padding: 0 6px;
    border-radius: 3px;
    color: #6b5a12;
    background-color: #f6efc8;
    font-weight: 600;
    line-height: 1.5;
    white-space: nowrap;
    cursor: help;
  }

  // A changed row has a bar on its left and a tag beside its range, so that
  // the change shows in black and white too.
  .panel-row-changed {
    padding-left: 8px;
    margin-left: -8px;
    box-shadow: inset 3px 0 0 var(--change-bar);
    background-color: var(--change-fill);
  }

  .panel-row-added {
    --change-bar: #3d9a52;
    --change-fill: rgba(61, 154, 82, 0.1);
  }

  .panel-row-narrowed {
    --change-bar: #3a74c4;
    --change-fill: rgba(58, 116, 196, 0.1);
  }

  .panel-change {
    display: inline-block;
    padding: 0 5px;
    margin-left: 6px;
    border-radius: 3px;
    font-family: sans-serif;
    line-height: 1.5;
    white-space: nowrap;
  }

  .panel-change-added {
    color: #1d5c2a;
    background-color: #dff1e3;
  }

  .panel-change-narrowed {
    color: #1f4e8c;
    background-color: #e0ebfa;
  }

  .panel-empty {
    line-height: 1.35;
  }

  // The snapshot and the What-If section share the height; the snapshot
  // keeps enough of it to stay readable, and each scrolls on its own.
  .panel-scroll-area {
    position: relative;
    display: flex;
    flex: 1 1 0;
    flex-direction: column;
    min-height: 120px;
  }

  .panel-what-if {
    flex: 0 1 auto;
    max-height: 55%;
  }

  // Shows that a scrolled area cuts content off at that edge.
  .scroll-shadow {
    position: absolute;
    z-index: 1;
    right: 0;
    left: 0;
    height: 12px;
    pointer-events: none;
  }

  .scroll-shadow-top {
    top: 0;
    background: linear-gradient(rgba(0, 0, 0, 0.16), rgba(0, 0, 0, 0));
  }

  .scroll-shadow-bottom {
    bottom: 0;
    background: linear-gradient(rgba(0, 0, 0, 0), rgba(0, 0, 0, 0.16));
  }
}
</style>
