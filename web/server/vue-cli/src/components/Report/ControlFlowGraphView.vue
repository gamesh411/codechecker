<template>
  <v-card
    class="cfg-view d-flex flex-column"
    variant="flat"
    rounded="0"
    role="region"
    aria-label="Control flow graph"
  >
    <div class="cfg-header d-flex align-center px-3 py-1">
      <span
        class="text-subtitle-2 text-no-wrap"
        title="Control flow graph"
      >
        {{ compactHeader ? "CFG" : "Control flow graph" }}
      </span>
      <select
        v-if="cfgs.length > 1"
        class="cfg-function-select ml-2"
        aria-label="Function"
        :value="cfgIndex"
        @change="emit('update:cfgIndex', Number($event.target.value))"
      >
        <option
          v-for="(graphOption, index) in cfgs"
          :key="index"
          :value="index"
        >
          {{ graphOption.function }}
        </option>
      </select>
      <span
        v-else
        class="cfg-function ml-2 text-body-2"
        :title="cfg.function"
      >
        {{ cfg.function }}
      </span>
      <v-spacer />
      <v-btn
        icon="mdi-close"
        variant="text"
        size="small"
        density="compact"
        aria-label="Close control flow graph"
        title="Close"
        @click="emit('close')"
      />
    </div>

    <v-divider />

    <div class="cfg-toolbar d-flex align-center px-3 text-caption">
      <span class="cfg-legend">
        <span class="cfg-legend-swatch cfg-legend-path" />Bug Path
        <span class="cfg-legend-swatch cfg-legend-step ml-2" />Path Step
      </span>
      <v-spacer />
      <label
        class="cfg-subexpressions"
        :title="subexpressionsHint"
      >
        <input v-model="showSubexpressions" type="checkbox">
        Subexpressions
      </label>
    </div>

    <v-divider />

    <div ref="scroller" class="cfg-scroller">
      <div
        ref="graph"
        class="cfg-graph"
        :style="{ paddingLeft: `${gutterWidth}px` }"
      >
        <svg
          class="cfg-edges"
          :width="gutterWidth + 40"
          :height="graphHeight"
          aria-hidden="true"
        >
          <defs>
            <marker
              v-for="kind in [ 'path', 'other' ]"
              :id="`cfg-arrow-${kind}`"
              :key="kind"
              viewBox="0 0 8 8"
              refX="7"
              refY="4"
              markerWidth="7"
              markerHeight="7"
              orient="auto-start-reverse"
            >
              <path d="M0,0 L8,4 L0,8 z" :class="`cfg-arrow-${kind}`" />
            </marker>
          </defs>
          <g
            v-for="edge in drawnEdges"
            :key="edge.key"
            :class="[ 'cfg-edge', edge.onPath ? 'on-path' : 'other' ]"
          >
            <title>{{ edge.title }}</title>
            <path
              :d="edge.d"
              :marker-end="`url(#cfg-arrow-${edge.onPath ? 'path' : 'other'})`"
            />
            <text
              v-if="edge.label"
              :x="edge.labelX"
              :y="edge.labelY"
              class="cfg-edge-label"
            >
              {{ edge.label }}
            </text>
          </g>
        </svg>

        <div
          v-for="block in blocks"
          :key="block.id"
          :ref="el => setBlockRef(block.id, el)"
          :class="[
            'cfg-block',
            block.onPath ? 'on-path' : 'off-path'
          ]"
        >
          <div class="cfg-block-header">
            <span class="cfg-block-id">B{{ block.id }}</span>
            <span v-if="block.id === cfg.entry" class="cfg-block-kind">
              entry
            </span>
            <span v-else-if="block.id === cfg.exit" class="cfg-block-kind">
              exit
            </span>
            <span v-if="block.lines" class="cfg-block-lines">
              {{ block.lines }}
            </span>
          </div>

          <component
            :is="row.choices.length ? 'button' : 'div'"
            v-for="row in block.rows"
            :key="row.key"
            :type="row.choices.length ? 'button' : undefined"
            :class="[
              'cfg-row',
              `cfg-row-${row.kind}`,
              {
                'has-steps': row.choices.length,
                selected: row.key === selectedRowKey
              }
            ]"
            :title="row.title"
            :data-row="row.key"
            @click="row.choices.length && emit('select', row.choices, $event)"
          >
            <span class="cfg-row-index">{{ row.index }}</span>
            <span class="cfg-row-text">{{ row.text }}</span>
            <span v-if="row.line" class="cfg-row-line">{{ row.line }}</span>
            <span v-if="row.choices.length" class="cfg-row-steps">
              {{ stepsLabel(row) }}
            </span>
          </component>
        </div>
      </div>
    </div>
  </v-card>
</template>

<script setup>
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch
} from "vue";
import { useDisplay } from "vuetify";

const props = defineProps({
  // The trace's `cfgs`.
  cfgs: { type: Array, required: true },
  cfgIndex: { type: Number, required: true },
  // Path Steps in Bug Path order, as Report.vue collects them.
  steps: { type: Array, required: true },
  selectedOrder: { type: Number, default: null },
  // Several steps at one element that would show the same panel are one
  // choice; Report.vue's rule for lines.
  oneChoicePerSnapshot: { type: Function, required: true }
});

const emit = defineEmits([ "close", "select", "update:cfgIndex" ]);

// Below 1280px the function name needs the room more than the title.
const { mdAndDown: compactHeader } = useDisplay();

const scroller = ref(null);
const graph = ref(null);
const showSubexpressions = ref(false);
const blockElements = new Map();
const blockBoxes = ref(new Map());
const graphHeight = ref(0);

const subexpressionsHint = "Show every element of the linearized CFG, " +
  "as -analyzer-checker=debug.DumpCFG lists them";

const LANE_GAP = 8;
const GUTTER_PADDING = 14;
const FALL_THROUGH_X = 18;

const cfg = computed(() => props.cfgs[props.cfgIndex] ?? props.cfgs[0]);

// The steps at each place of this CFG, keyed like the rows.
const stepsByPlace = computed(() => {
  const _byPlace = new Map();
  props.steps.forEach(step => {
    const _position = step.snapshot.cfg_position;
    if (!_position || props.cfgs[_position.cfg] !== cfg.value) return;
    const _key = placeKey(_position);
    if (!_byPlace.has(_key)) _byPlace.set(_key, []);
    _byPlace.get(_key).push(step);
  });
  return _byPlace;
});

// Of several choices the selected one is told by its place among them, since
// the panel already names it.
function stepsLabel(row) {
  if (row.choices.length === 1) return row.choices[0].label;
  const _selected = row.choices.findIndex(step =>
    step.order === props.selectedOrder);
  return _selected < 0
    ? `${row.choices.length} steps`
    : `${_selected + 1} of ${row.choices.length} steps`;
}

function placeKey({ block, element, terminator }) {
  if (element !== undefined) return `${block}.${element}`;
  return terminator ? `${block}.T` : `${block}.entry`;
}

const blocks = computed(() => {
  const _onPath = new Set(cfg.value.path_blocks ?? []);
  return cfg.value.blocks.map(block => {
    const _rows = [];
    const _entrySteps = stepsByPlace.value.get(`${block.id}.entry`) ?? [];
    if (_entrySteps.length) {
      _rows.push({
        key: `${block.id}.entry`,
        kind: "entry",
        index: "",
        text: "on entering the block",
        title: "Path Steps where the Bug Path enters this block",
        steps: _entrySteps
      });
    }

    // A hidden subexpression's steps go to the statement it is part of.
    const _shownElement = index => {
      if (showSubexpressions.value) return index;
      let _current = index;
      while (block.elements[_current].parent !== undefined) {
        _current = block.elements[_current].parent;
      }
      return _current;
    };
    const _elementSteps = new Map();
    block.elements.forEach((_, index) => {
      const _steps = stepsByPlace.value.get(`${block.id}.${index}`) ?? [];
      if (!_steps.length) return;
      const _shown = _shownElement(index);
      _elementSteps.set(_shown,
        [ ...(_elementSteps.get(_shown) ?? []), ..._steps ]);
    });

    block.elements.forEach((element, index) => {
      if (!showSubexpressions.value && element.parent !== undefined) return;
      _rows.push({
        key: `${block.id}.${index}`,
        kind: element.parent === undefined ? "statement" : "subexpression",
        index: `${index + 1}`,
        text: element.text,
        line: element.line ? `L${element.line}` : "",
        title: element.line
          ? `B${block.id}.${index + 1}, line ${element.line}: ${element.text}`
          : `B${block.id}.${index + 1}: ${element.text}`,
        steps: _elementSteps.get(index) ?? []
      });
    });

    if (block.terminator !== undefined) {
      _rows.push({
        key: `${block.id}.T`,
        kind: "terminator",
        index: "T",
        text: block.terminator,
        title: `B${block.id} terminator: ${block.terminator}`,
        steps: stepsByPlace.value.get(`${block.id}.T`) ?? []
      });
    }

    _rows.forEach(row => {
      row.choices = props.oneChoicePerSnapshot(
        [ ...row.steps ].sort((a, b) => a.order - b.order));
    });

    const _lines = block.elements.map(element => element.line)
      .filter(Boolean);
    const _first = Math.min(..._lines);
    const _last = Math.max(..._lines);

    return {
      id: block.id,
      successors: block.successors,
      onPath: _onPath.has(block.id),
      lines: !_lines.length ? ""
        : _first === _last ? `line ${_first}` : `lines ${_first}–${_last}`,
      rows: _rows
    };
  });
});

const selectedRowKey = computed(() => {
  if (props.selectedOrder === null) return null;
  for (const block of blocks.value) {
    const _row = block.rows.find(row =>
      row.steps.some(step => step.order === props.selectedOrder));
    if (_row) return _row.key;
  }
  return null;
});

// Edges to the next block down are short arrows between the two; every other
// edge runs in a lane of the left gutter, shorter spans closer to the blocks,
// so lanes cross as little as possible.
const routedEdges = computed(() => {
  const _rowOf = new Map(blocks.value.map((block, row) => [ block.id, row ]));
  const _pathEdges = new Set((cfg.value.path_edges ?? [])
    .map(edge => `${edge.from}-${edge.to}`));

  const _edges = [];
  blocks.value.forEach((block, row) => {
    const _branch = block.successors.length === 2 &&
      cfg.value.blocks[row].terminator !== undefined;
    block.successors.forEach((to, index) => {
      if (to < 0 || !_rowOf.has(to)) return;
      _edges.push({
        key: `${block.id}-${to}-${index}`,
        from: block.id,
        to,
        fromRow: row,
        toRow: _rowOf.get(to),
        onPath: _pathEdges.has(`${block.id}-${to}`),
        label: _branch ? (index === 0 ? "T" : "F") : "",
        fallThrough: _rowOf.get(to) === row + 1
      });
    });
  });

  const _lanes = [];
  _edges.filter(edge => !edge.fallThrough)
    .sort((a, b) =>
      Math.abs(a.toRow - a.fromRow) - Math.abs(b.toRow - b.fromRow))
    .forEach(edge => {
      const _low = Math.min(edge.fromRow, edge.toRow);
      const _high = Math.max(edge.fromRow, edge.toRow);
      let _lane = _lanes.findIndex(spans =>
        spans.every(([ low, high ]) => _high < low || _low > high));
      if (_lane < 0) {
        _lane = _lanes.length;
        _lanes.push([]);
      }
      _lanes[_lane].push([ _low, _high ]);
      edge.lane = _lane;
    });

  return { edges: _edges, laneCount: _lanes.length };
});

const gutterWidth = computed(() =>
  GUTTER_PADDING + routedEdges.value.laneCount * LANE_GAP + 6);

const drawnEdges = computed(() => {
  const _boxes = blockBoxes.value;
  if (!_boxes.size) return [];
  const _left = gutterWidth.value;

  // Several gutter edges leave or enter one block; spread their ends apart.
  const _outSlots = new Map();
  const _inSlots = new Map();
  const _slot = (slots, id) => {
    const _next = slots.get(id) ?? 0;
    slots.set(id, _next + 1);
    return _next;
  };

  const _drawn = routedEdges.value.edges.map(edge => {
    const _from = _boxes.get(edge.from);
    const _to = _boxes.get(edge.to);
    if (!_from || !_to) return null;
    const _title = `B${edge.from} → B${edge.to}` +
      (edge.label ? ` (${edge.label === "T" ? "true" : "false"})` : "") +
      (edge.onPath ? ", taken by the Bug Path" : "");

    if (edge.fallThrough) {
      const _x = _left + FALL_THROUGH_X;
      return {
        ...edge,
        title: _title,
        d: `M${_x},${_from.bottom} L${_x},${_to.top}`,
        labelX: _x + 5,
        labelY: _from.bottom + 9
      };
    }

    const _laneX = _left - 6 - edge.lane * LANE_GAP;
    const _y1 = _from.bottom - 8 - _slot(_outSlots, edge.from) * 6;
    const _y2 = _to.top + 10 + _slot(_inSlots, edge.to) * 6;
    return {
      ...edge,
      title: _title,
      d: `M${_left},${_y1} H${_laneX} V${_y2} H${_left}`,
      labelX: _left - 9,
      labelY: _y1 - 2
    };
  }).filter(Boolean);

  // Edges the Bug Path took are drawn last, so they stay on top.
  return [ ..._drawn.filter(edge => !edge.onPath),
    ..._drawn.filter(edge => edge.onPath) ];
});

function setBlockRef(id, el) {
  if (el) blockElements.set(id, el);
  else blockElements.delete(id);
}

function measure() {
  const _boxes = new Map();
  blockElements.forEach((el, id) => {
    _boxes.set(id,
      { top: el.offsetTop, bottom: el.offsetTop + el.offsetHeight });
  });
  blockBoxes.value = _boxes;
  graphHeight.value = graph.value?.scrollHeight ?? 0;
}

function scrollToSelection() {
  if (!selectedRowKey.value || !scroller.value) return;
  const _row = scroller.value.querySelector(
    `[data-row="${selectedRowKey.value}"]`);
  if (!_row) return;
  const _rowBox = _row.getBoundingClientRect();
  const _viewBox = scroller.value.getBoundingClientRect();
  if (_rowBox.top < _viewBox.top || _rowBox.bottom > _viewBox.bottom) {
    scroller.value.scrollTop += _rowBox.top - _viewBox.top -
      (_viewBox.height - _rowBox.height) / 2;
  }
}

let resizeObserver = null;

onMounted(async () => {
  resizeObserver = new ResizeObserver(measure);
  resizeObserver.observe(graph.value);
  await nextTick();
  measure();
  scrollToSelection();
});

onBeforeUnmount(() => resizeObserver?.disconnect());

watch([ blocks, gutterWidth ], async () => {
  await nextTick();
  measure();
});

watch(() => [ props.cfgIndex, selectedRowKey.value, showSubexpressions.value ],
  async () => {
    await nextTick();
    measure();
    scrollToSelection();
  });
</script>

<style lang="scss">
.cfg-view {
  border: 1px solid #d8dbe0;
  box-shadow: -2px 0 8px rgba(0, 0, 0, 0.12);

  .cfg-header {
    min-height: 40px;
    background-color: #f7f7f7;
  }

  .cfg-function,
  .cfg-function-select {
    min-width: 0;
    overflow: hidden;
    font-family: monospace;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .cfg-function-select {
    max-width: 60%;
    padding: 1px 4px;
    border: 1px solid #c4c8ce;
    border-radius: 3px;
    background-color: #fff;
  }

  .cfg-toolbar {
    min-height: 30px;
    gap: 8px;
    color: rgba(0, 0, 0, 0.6);
  }

  .cfg-legend {
    white-space: nowrap;
  }

  .cfg-legend-swatch {
    display: inline-block;
    width: 10px;
    height: 10px;
    margin-right: 4px;
    border-radius: 2px;
    vertical-align: -1px;
  }

  .cfg-legend-path {
    border: 2px solid var(--color-report-step-message-default);
  }

  .cfg-legend-step {
    background-color: var(--color-report-step-message-default-light);
  }

  .cfg-subexpressions {
    display: flex;
    gap: 4px;
    align-items: center;
    white-space: nowrap;
    cursor: pointer;
  }

  .cfg-scroller {
    flex: 1 1 0;
    min-height: 0;
    overflow: auto;
  }

  .cfg-graph {
    position: relative;
    padding: 10px 10px 16px 0;
  }

  .cfg-edges {
    position: absolute;
    top: 0;
    left: 0;
    overflow: visible;
    pointer-events: none;

    .cfg-edge path:not([class]) {
      fill: none;
    }

    .cfg-edge.other path {
      stroke: #a3a9b1;
      stroke-width: 1;
    }

    .cfg-edge.on-path path {
      stroke: var(--color-report-step-message-default);
      stroke-width: 2;
    }

    .cfg-arrow-path {
      fill: var(--color-report-step-message-default);
    }

    .cfg-arrow-other {
      fill: #a3a9b1;
    }

    .cfg-edge-label {
      font-size: 9px;
      font-weight: 600;
      fill: rgba(0, 0, 0, 0.55);
    }
  }

  .cfg-block {
    position: relative;
    min-width: 0;
    border: 1px solid #c4c8ce;
    border-radius: 4px;
    background-color: #fff;
    font-size: 12px;

    & + .cfg-block {
      margin-top: 16px;
    }

    &.on-path {
      border: 2px solid var(--color-report-step-message-default);
    }

    &.off-path {
      color: rgba(0, 0, 0, 0.5);
      background-color: #fafafa;
    }
  }

  .cfg-block-header {
    display: flex;
    gap: 6px;
    align-items: baseline;
    padding: 2px 8px;
    border-bottom: 1px solid #e3e5e8;
  }

  .cfg-block-id {
    font-weight: 600;
  }

  .cfg-block-kind {
    color: rgba(0, 0, 0, 0.6);
    font-style: italic;
  }

  .cfg-block-lines {
    margin-left: auto;
    color: rgba(0, 0, 0, 0.5);
    white-space: nowrap;
  }

  .cfg-row {
    display: flex;
    gap: 6px;
    align-items: baseline;
    width: 100%;
    min-width: 0;
    padding: 1px 8px;
    border: 0;
    background: none;
    color: inherit;
    font: inherit;
    line-height: 1.6;
    text-align: left;

    &.has-steps {
      background-color: var(--color-report-step-message-default-light);
      cursor: pointer;

      &:hover {
        background-color: #afd0dd;
      }
    }

    &.selected {
      background-color: #fff6d9;
      box-shadow: inset 3px 0 0 #e0a800;

      &:hover {
        background-color: #ffefbf;
      }
    }

    &:focus-visible {
      outline: 2px solid var(--color-report-step-message-default);
      outline-offset: -2px;
    }
  }

  .cfg-row-index {
    flex: none;
    width: 1.8em;
    color: rgba(0, 0, 0, 0.45);
    text-align: right;
  }

  .cfg-row-text {
    flex: 1 1 auto;
    min-width: 0;
    overflow: hidden;
    font-family: monospace;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .cfg-row-subexpression .cfg-row-text {
    color: rgba(0, 0, 0, 0.6);
  }

  .cfg-row-terminator .cfg-row-text {
    font-weight: 600;
  }

  .cfg-row-entry .cfg-row-text {
    font-family: inherit;
    font-style: italic;
  }

  .cfg-row-line {
    flex: none;
    color: rgba(0, 0, 0, 0.45);
  }

  .cfg-row-steps {
    flex: none;
    font-weight: 600;
    white-space: nowrap;
  }
}
</style>
