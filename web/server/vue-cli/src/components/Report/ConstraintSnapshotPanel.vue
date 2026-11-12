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

            <div v-if="variableRows.length" class="panel-group">
              <h5 class="panel-group-title text-caption">
                Variables
              </h5>
              <div
                v-for="row in variableRows"
                :key="row.name"
                class="panel-row"
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
                  <div
                    v-if="row.symbol"
                    class="panel-symbol"
                    :title="row.symbol"
                  >
                    <dump-text :text="row.symbol" />
                  </div>
                </div>
              </div>
            </div>

            <div v-if="otherSymbols.length" class="panel-group">
              <h5 class="panel-group-title text-caption">
                Other symbols
              </h5>
              <div
                v-for="range in otherSymbols"
                :key="range.symbol"
                class="panel-row panel-row-raw"
              >
                <div class="panel-name panel-raw-symbol" :title="range.symbol">
                  <dump-text :text="range.symbol" />
                </div>
                <div class="panel-value">
                  <range-text :range="range.range" :symbol="range.symbol" />
                </div>
              </div>
            </div>

            <p
              v-if="!variableRows.length && !otherSymbols.length"
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
              v-for="row in accumulatedRows"
              :key="row.symbol"
              class="panel-row"
              :class="{ 'panel-row-raw': !row.name }"
            >
              <div
                class="panel-name"
                :class="{ 'panel-raw-symbol': !row.name }"
                :title="row.symbol"
              >
                <dump-text :text="row.name ?? row.symbol" />
              </div>
              <div class="panel-value">
                <range-text :range="row.range" :symbol="row.symbol" />
                <div class="panel-meta">
                  <span
                    v-if="row.name"
                    class="panel-symbol"
                    :title="row.symbol"
                  >
                    <dump-text :text="row.symbol" />
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
              v-if="!accumulatedRows.length"
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
import { computed, h, ref } from "vue";
import { useDisplay } from "vuetify";

import { useScrollShadows } from "@/composables/useScrollShadows";

import { compareNames, readableRange, shortenPaths } from "./ConstraintTrace";
import WhatIfSection from "./WhatIfSection";

const props = defineProps({
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

const DROPPED_HINT = "The analyzer no longer tracks this value at this step";

const body = ref(null);
const { canScrollUp, canScrollDown } = useScrollShadows(body);

// A symbol dump starts with its type, as in reg_$1<int * g> or
// conj_$2{void *, ...}. The type stops at a brace or angle bracket, so a
// pointer inside the region of a non-pointer field does not count.
const POINTER_SYMBOL = /^[a-z]+_\$\d+[<{][^,{}<>]*\*/;
const POINTER_WORDS = new Map([
  [ "{ [0, 0] }", "null" ],
  [ "{ [1, 18446744073709551615] }", "non-null" ]
]);

// One span per interval, so a long range set wraps only between intervals.
// A pointer's null and non-null ranges, and an integer range that readableRange
// can say in words, read as words; the title keeps the analyzer's range.
function RangeText({ range, symbol }) {
  const _word = symbol && POINTER_SYMBOL.test(symbol)
    ? POINTER_WORDS.get(range) : undefined;
  if (_word) return h("span", { title: range }, _word);

  const _readable = readableRange(range, symbol);
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

const knownRanges = computed(() =>
  new Map(props.step.symbolRanges.map(range => [ range.symbol, range.range ]))
);

const variableRows = computed(() =>
  [ ...props.step.variables ]
    .sort((a, b) => compareNames(a.name, b.name))
    .map(variable => {
      const _isSymbol = variable.kind === "symbol";
      return {
        name: variable.name,
        range: _isSymbol ? knownRanges.value.get(variable.value) : undefined,
        display: _isSymbol ? "no known range" : variable.value,
        symbol: _isSymbol ? variable.value : null,
        muted: _isSymbol && !knownRanges.value.has(variable.value)
      };
    })
);

const otherSymbols = computed(() => {
  const _named = new Set(props.step.variables.map(variable => variable.value));
  return props.step.symbolRanges
    .filter(range => !_named.has(range.symbol))
    .sort((a, b) => compareNames(a.symbol, b.symbol));
});

// Named rows first, as variables come before other symbols above, each in
// the order variables are.
const accumulatedRows = computed(() =>
  props.step.pathCondition.map(range => ({
    symbol: range.symbol,
    range: range.range,
    name: props.step.symbolNames.get(range.symbol),
    dropped: !knownRanges.value.has(range.symbol)
  })).sort((a, b) =>
    (a.name === undefined) - (b.name === undefined) ||
    compareNames(a.name ?? a.symbol, b.name ?? b.symbol))
);
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

  .panel-dropped {
    padding: 0 5px;
    border-radius: 3px;
    color: #6b5a12;
    background-color: #f6efc8;
    white-space: nowrap;
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
