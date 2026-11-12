<template>
  <report-side-view
    ref="sideView"
    class="cfg-view"
    title="Control flow graph"
    short-title="CFG"
    @close="emit('close')"
  >
    <template #header>
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
    </template>

    <template #toolbar>
      <span class="side-view-legend">
        <span
          class="side-view-legend-swatch side-view-legend-path"
        />Bug Path
        <span
          class="side-view-legend-swatch side-view-legend-step ml-2"
        />Path Step
      </span>
      <v-spacer />
      <label
        class="cfg-subexpressions"
        :title="subexpressionsHint"
      >
        <input v-model="showSubexpressions" type="checkbox">
        Subexpressions
      </label>
    </template>

    <div
      class="cfg-graph"
      :style="{ height: `${layout.height}px` }"
    >
      <svg
        class="cfg-edges"
        :width="gutterWidth + 40"
        :height="layout.height"
        aria-hidden="true"
      >
        <defs>
          <!-- Sized in pixels rather than by the line's width, so that the
            thicker Bug Path edges do not get heads big enough to cover a
            block's border or an edge's label. -->
          <marker
            v-for="kind in [ 'path', 'other' ]"
            :id="`cfg-arrow-${kind}`"
            :key="kind"
            viewBox="0 0 6 6"
            refX="6"
            refY="3"
            markerUnits="userSpaceOnUse"
            markerWidth="6"
            markerHeight="6"
            orient="auto-start-reverse"
          >
            <path d="M0,0 L6,3 L0,6 z" :class="`cfg-arrow-${kind}`" />
          </marker>
        </defs>
        <g
          v-for="edge in visibleEdges"
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
        v-for="block in visibleBlocks"
        :key="block.id"
        :class="[
          'cfg-block',
          block.onPath ? 'on-path' : 'off-path'
        ]"
        :style="{
          top: `${block.top}px`,
          height: `${block.height}px`,
          left: `${gutterWidth}px`
        }"
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

        <div
          class="cfg-rows"
          :style="{ paddingTop: `${block.firstRow * ROW_HEIGHT}px` }"
        >
          <component
            :is="row.choices.length ? 'button' : 'div'"
            v-for="row in block.shownRows"
            :key="row.key"
            :type="row.choices.length ? 'button' : undefined"
            :class="[
              'cfg-row',
              'side-view-choice',
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
  </report-side-view>
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
import ReportSideView from "./ReportSideView";

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

const sideView = ref(null);
const showSubexpressions = ref(false);

const subexpressionsHint = "Show every element of the linearized CFG, " +
  "as -analyzer-checker=debug.DumpCFG lists them";

const LANE_GAP = 8;
const GUTTER_PADDING = 14;
const FALL_THROUGH_X = 18;

// The graph is laid out from these rather than measured, so that only the
// blocks and rows in view need to be rendered: a function can have thousands
// of blocks, and a block tens of thousands of subexpressions. The styles
// below give the elements these sizes.
const ROW_HEIGHT = 20;
const HEADER_HEIGHT = 21;
const BLOCK_GAP = 16;
const GRAPH_PADDING_TOP = 10;
const GRAPH_PADDING_BOTTOM = 16;
// Rendered beyond each edge of the view, so a scroll shows no gap before the
// next frame fills it.
const OVERSCAN = 600;
// Where edges join a block: incoming ones at its header, outgoing ones at its
// last row, which is the terminator of a branch. Stubs of one block are this
// far apart.
const ENTRY_OFFSET = 5;
const STUB_GAP = 8;

const cfg = computed(() => props.cfgs[props.cfgIndex] ?? props.cfgs[0]);

// The steps at each place of this CFG, keyed like the rows.
const stepsByPlace = computed(() => {
  const _byPlace = new Map();
  props.steps.forEach(step => {
    const _position = step.cfgPosition;
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
      row.choices = row.steps.length
        ? props.oneChoicePerSnapshot(
          [ ...row.steps ].sort((a, b) => a.order - b.order))
        : [];
    });

    let _first = Infinity;
    let _last = -Infinity;
    block.elements.forEach(({ line }) => {
      if (!line) return;
      _first = Math.min(_first, line);
      _last = Math.max(_last, line);
    });

    return {
      id: block.id,
      successors: block.successors,
      onPath: _onPath.has(block.id),
      lines: _first > _last ? ""
        : _first === _last ? `line ${_first}` : `lines ${_first}–${_last}`,
      rows: _rows
    };
  });
});

// Where each block sits, from the sizes above. Blocks on the Bug Path have a
// thicker border.
const layout = computed(() => {
  const _boxes = new Map();
  let _top = GRAPH_PADDING_TOP;
  const _placed = blocks.value.map(block => {
    const _border = block.onPath ? 2 : 1;
    const _height = 2 * _border + HEADER_HEIGHT +
      block.rows.length * ROW_HEIGHT;
    const _box = {
      top: _top,
      bottom: _top + _height,
      border: _border,
      rowsTop: _top + _border + HEADER_HEIGHT,
      rowCount: block.rows.length
    };
    _boxes.set(block.id, _box);
    _top += _height + BLOCK_GAP;
    return { block, box: _box };
  });
  return {
    placed: _placed,
    boxes: _boxes,
    height: _top - BLOCK_GAP + GRAPH_PADDING_BOTTOM
  };
});

// The scrolled view, in the graph's coordinates.
const view = ref({ top: 0, height: 0 });

const shownRange = computed(() => {
  // Before the first measurement, a screenful from the top.
  const _height = view.value.height || 1000;
  return [ view.value.top - OVERSCAN, view.value.top + _height + OVERSCAN ];
});

const visibleBlocks = computed(() => {
  const [ _low, _high ] = shownRange.value;
  return layout.value.placed
    .filter(({ box }) => box.bottom > _low && box.top < _high)
    .map(({ block, box }) => {
      const _clamp = row => Math.min(Math.max(row, 0), box.rowCount);
      const _first = _clamp(Math.floor((_low - box.rowsTop) / ROW_HEIGHT));
      const _last = _clamp(Math.ceil((_high - box.rowsTop) / ROW_HEIGHT));
      return {
        ...block,
        top: box.top,
        height: box.bottom - box.top,
        firstRow: _first,
        shownRows: block.rows.slice(_first, _last)
      };
    });
});

const selectedRow = computed(() => {
  if (props.selectedOrder === null) return null;
  for (const block of blocks.value) {
    const _index = block.rows.findIndex(row =>
      row.steps.some(step => step.order === props.selectedOrder));
    if (_index >= 0) return { block: block.id, index: _index,
      key: block.rows[_index].key };
  }
  return null;
});

const selectedRowKey = computed(() => selectedRow.value?.key ?? null);

// Edges to the next block down are short arrows between the two; every other
// edge runs in a lane of the left gutter, shorter spans closer to the blocks,
// so lanes cross as little as possible. Edges into one block from the same
// side share a lane and join before they enter it, the way many early
// returns reach the exit, so that the gutter does not grow with their count.
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

  const _groups = new Map();
  _edges.filter(edge => !edge.fallThrough).forEach(edge => {
    edge.group = `${edge.to}-${edge.toRow > edge.fromRow ? "down" : "up"}`;
    const _group = _groups.get(edge.group) ??
      { low: Infinity, high: -Infinity, edges: [] };
    _group.low = Math.min(_group.low, edge.fromRow, edge.toRow);
    _group.high = Math.max(_group.high, edge.fromRow, edge.toRow);
    _group.edges.push(edge);
    _groups.set(edge.group, _group);
  });

  const _lanes = [];
  [ ..._groups.values() ]
    .sort((a, b) => (a.high - a.low) - (b.high - b.low))
    .forEach(group => {
      let _lane = _lanes.findIndex(spans =>
        spans.every(([ low, high ]) => group.high < low || group.low > high));
      if (_lane < 0) {
        _lane = _lanes.length;
        _lanes.push([]);
      }
      _lanes[_lane].push([ group.low, group.high ]);
      group.edges.forEach(edge => {
        edge.lane = _lane;
      });
    });

  return { edges: _edges, laneCount: _lanes.length };
});

const gutterWidth = computed(() =>
  GUTTER_PADDING + routedEdges.value.laneCount * LANE_GAP + 6);

const drawnEdges = computed(() => {
  const _boxes = layout.value.boxes;
  const _left = gutterWidth.value;

  // Several gutter edges leave or enter one block; spread their ends apart.
  const _outSlots = new Map();
  const _inSlots = new Map();
  const _slot = (slots, id) => {
    const _next = slots.get(id) ?? 0;
    slots.set(id, _next + 1);
    return _next;
  };
  // The edges of one group enter their block at one point.
  const _entries = new Map();

  const _drawn = routedEdges.value.edges.map(edge => {
    const _from = _boxes.get(edge.from);
    const _to = _boxes.get(edge.to);
    const _title = `B${edge.from} → B${edge.to}` +
      (edge.label ? ` (${edge.label === "T" ? "true" : "false"})` : "") +
      (edge.onPath ? ", taken by the Bug Path" : "");

    if (edge.fallThrough) {
      const _x = _left + FALL_THROUGH_X;
      // Beside the line, so the arrowhead below it stays clear.
      return {
        ...edge,
        title: _title,
        d: `M${_x},${_from.bottom} L${_x},${_to.top}`,
        labelX: _x + 5,
        labelY: _from.bottom + 11,
        low: _from.bottom,
        high: _to.top
      };
    }

    const _laneX = _left - 6 - edge.lane * LANE_GAP;
    // From the last row, or from below the entries of a block without rows.
    const _base = _from.rowCount
      ? _from.bottom - _from.border - ROW_HEIGHT / 2
      : _from.rowsTop - 3;
    const _outSlot = _slot(_outSlots, edge.from);
    const _y1 = _base - _outSlot * STUB_GAP;
    if (!_entries.has(edge.group)) {
      _entries.set(edge.group, _to.top + _to.border + ENTRY_OFFSET +
        _slot(_inSlots, edge.to) * STUB_GAP);
    }
    const _y2 = _entries.get(edge.group);
    // The first stub's label goes below it and later ones above theirs, so
    // that no label sits on another stub.
    return {
      ...edge,
      title: _title,
      d: `M${_left},${_y1} H${_laneX} V${_y2} H${_left}`,
      labelX: _left - 9,
      labelY: _outSlot ? _y1 - 2 : _y1 + 8,
      low: Math.min(_y1, _y2) - STUB_GAP,
      high: Math.max(_y1, _y2) + STUB_GAP
    };
  });

  // Edges the Bug Path took are drawn last, so they stay on top.
  return [ ..._drawn.filter(edge => !edge.onPath),
    ..._drawn.filter(edge => edge.onPath) ];
});

const visibleEdges = computed(() => {
  const [ _low, _high ] = shownRange.value;
  return drawnEdges.value.filter(edge =>
    edge.high > _low && edge.low < _high);
});

function updateView() {
  const _scroller = sideView.value?.scroller;
  if (!_scroller) return;
  const _top = _scroller.scrollTop;
  const _height = _scroller.clientHeight;
  if (_top !== view.value.top || _height !== view.value.height) {
    view.value = { top: _top, height: _height };
  }
}

let frameRequest = null;
function onScroll() {
  if (frameRequest !== null) return;
  frameRequest = requestAnimationFrame(() => {
    frameRequest = null;
    updateView();
  });
}

function scrollToSelection() {
  const _selected = selectedRow.value;
  if (!_selected) return;
  const _box = layout.value.boxes.get(_selected.block);
  const _top = _box.rowsTop + _selected.index * ROW_HEIGHT;
  sideView.value?.revealRange(_top, _top + ROW_HEIGHT);
  updateView();
}

let resizeObserver = null;

onMounted(async () => {
  const _scroller = sideView.value.scroller;
  _scroller.addEventListener("scroll", onScroll, { passive: true });
  resizeObserver = new ResizeObserver(updateView);
  resizeObserver.observe(_scroller);
  await nextTick();
  updateView();
  scrollToSelection();
});

onBeforeUnmount(() => {
  sideView.value?.scroller?.removeEventListener("scroll", onScroll);
  resizeObserver?.disconnect();
  if (frameRequest !== null) cancelAnimationFrame(frameRequest);
});

watch(() => [ props.cfgIndex, selectedRowKey.value, showSubexpressions.value ],
  async () => {
    await nextTick();
    // A shorter graph can leave the view scrolled past its end.
    updateView();
    scrollToSelection();
  });
</script>

<style lang="scss">
.cfg-view {
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

  .cfg-subexpressions {
    display: flex;
    gap: 4px;
    align-items: center;
    white-space: nowrap;
    cursor: pointer;
  }

  .cfg-graph {
    position: relative;
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

  // Placed and sized by the component's layout, which these sizes must match.
  .cfg-block {
    position: absolute;
    right: 10px;
    box-sizing: border-box;
    overflow: hidden;
    border: 1px solid #c4c8ce;
    border-radius: 4px;
    background-color: #fff;
    font-size: 12px;

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
    box-sizing: border-box;
    height: 21px;
    padding: 0 8px;
    border-bottom: 1px solid #e3e5e8;
    line-height: 20px;
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
    box-sizing: border-box;
    width: 100%;
    height: 20px;
    min-width: 0;
    padding: 0 8px;
    line-height: 20px;
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
