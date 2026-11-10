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
      {{ step.message }}
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
                  {{ row.name }}
                </div>
                <div class="panel-value">
                  <span :class="{ 'text-medium-emphasis': row.muted }">
                    <range-text v-if="row.range" :range="row.range" />
                    <template v-else>{{ row.display }}</template>
                  </span>
                  <div v-if="row.symbol" class="panel-symbol">
                    {{ row.symbol }}
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
                  {{ range.symbol }}
                </div>
                <div class="panel-value">
                  <range-text :range="range.range" />
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
                {{ row.name ?? row.symbol }}
              </div>
              <div class="panel-value">
                <range-text :range="row.range" />
                <div class="panel-meta">
                  <span v-if="row.name" class="panel-symbol">
                    {{ row.symbol }}
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
    />
  </v-card>
</template>

<script setup>
import { computed, h, ref } from "vue";

import { useScrollShadows } from "@/composables/useScrollShadows";

import WhatIfSection from "./WhatIfSection";

const props = defineProps({
  step: { type: Object, required: true },
  reportId: { type: Object, required: true },
  // From useWhatIf, owned by the report view.
  whatIf: { type: Object, required: true }
});

const emit = defineEmits([ "close" ]);

const DROPPED_HINT = "The analyzer no longer tracks this value at this step";

const body = ref(null);
const { canScrollUp, canScrollDown } = useScrollShadows(body);

// One span per interval, so a long range set wraps only between intervals.
function RangeText({ range }) {
  const _intervals = range.match(/\[[^\]]*\]/g);
  if (!_intervals) return range;
  return h("span", { class: "panel-intervals", title: range },
    _intervals.map(interval =>
      h("span", { class: "panel-interval" }, interval)));
}
RangeText.props = [ "range" ];

const knownRanges = computed(() =>
  new Map(props.step.symbolRanges.map(range => [ range.symbol, range.range ]))
);

const variableRows = computed(() =>
  props.step.variables.map(variable => {
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
  return props.step.symbolRanges.filter(range => !_named.has(range.symbol));
});

const accumulatedRows = computed(() =>
  props.step.pathCondition.map(range => ({
    symbol: range.symbol,
    range: range.range,
    name: props.step.symbolNames.get(range.symbol),
    dropped: !knownRanges.value.has(range.symbol)
  }))
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

  .panel-step-index {
    font-weight: 500;
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

  .panel-row {
    display: grid;
    grid-template-columns: minmax(3.5rem, 26%) minmax(0, 1fr);
    column-gap: 8px;
    padding: 3px 0;
    font-size: 0.8125rem;
    border-bottom: 1px solid #edf0f3;
  }

  .panel-row-raw {
    grid-template-columns: minmax(0, 1fr) minmax(0, auto);
  }

  .panel-accumulated .panel-row {
    border-bottom-color: #dfe3e9;
  }

  .panel-row:last-child {
    border-bottom: 0;
  }

  .panel-name,
  .panel-value,
  .panel-symbol {
    font-family: monospace;
    overflow-wrap: anywhere;
  }

  .panel-name {
    font-weight: 600;
  }

  .panel-name.panel-raw-symbol {
    font-weight: 400;
  }

  .panel-intervals {
    display: flex;
    flex-wrap: wrap;
    column-gap: 0.5rem;
  }

  .panel-interval {
    white-space: nowrap;
  }

  .panel-symbol {
    color: rgba(0, 0, 0, 0.55);
    font-size: 0.75rem;
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
