<template>
  <report-side-view
    ref="sideView"
    class="egraph-view"
    title="Analyzer states"
    short-title="States"
    @close="emit('close')"
  >
    <template #toolbar>
      <span class="side-view-legend">
        <span class="side-view-legend-swatch side-view-legend-step" />Path Step
      </span>
      <v-spacer />
      <span class="egraph-summary" :title="summaryHint">
        {{ summary }}
      </span>
    </template>

    <div class="egraph-states">
      <template v-for="group in groups" :key="group.key">
        <div
          v-if="group.gapBefore"
          class="egraph-gap"
          :style="{ marginLeft: `${group.depth * INDENT}px` }"
        >
          {{ describeGap(group.gapBefore) }}
        </div>
        <div
          class="egraph-frame"
          :style="{ marginLeft: `${group.depth * INDENT}px` }"
        >
          <div class="egraph-frame-header" :title="group.contextTitle">
            <template
              v-for="(crumb, index) in group.crumbs"
              :key="index"
            >
              <span v-if="index" class="egraph-crumb-sep">›</span>
              <span class="egraph-crumb">
                <span class="egraph-crumb-function">{{ crumb.function }}</span>
                <span v-if="crumb.callSite" class="egraph-crumb-call">
                  called at {{ crumb.callSite }}
                </span>
              </span>
            </template>
          </div>

          <template v-for="state in group.states" :key="state.index">
            <div v-if="state.gapBefore" class="egraph-gap egraph-gap-inner">
              {{ describeGap(state.gapBefore) }}
            </div>
            <button
              type="button"
              :class="[
                'egraph-state',
                'side-view-choice',
                {
                  'has-steps': state.choices.length,
                  selected: state.index === selectedState
                }
              ]"
              :title="state.title"
              :data-state="state.index"
              :disabled="!state.choices.length"
              @click="emit('select', state.choices, $event)"
            >
              <span class="egraph-state-id">#{{ state.node.id }}</span>
              <span class="egraph-state-kind">{{ state.node.kind }}</span>
              <span class="egraph-state-location">{{ state.location }}</span>
              <span class="egraph-state-steps">{{ stepsLabel(state) }}</span>
            </button>
          </template>
        </div>
      </template>
    </div>
  </report-side-view>
</template>

<script setup>
import { computed, nextTick, onMounted, ref, watch } from "vue";

import ReportSideView from "./ReportSideView";

const props = defineProps({
  // The trace's `exploded_nodes` and `stack_frames`.
  nodes: { type: Array, required: true },
  frames: { type: Array, required: true },
  // Path Steps in Bug Path order, as Report.vue collects them.
  steps: { type: Array, required: true },
  selectedOrder: { type: Number, default: null },
  // Several steps of one state that would show the same panel are one
  // choice; Report.vue's rule for lines.
  oneChoicePerSnapshot: { type: Function, required: true }
});

const emit = defineEmits([ "close", "select" ]);

const INDENT = 14;

const sideView = ref(null);

const summary = computed(() => {
  const _length = pathLength.value;
  return `${props.nodes.length} of ${_length} nodes`;
});
const summaryHint = computed(() =>
  `The ${props.nodes.length} exploded graph nodes that Path Steps come from, ` +
  `out of the ${pathLength.value} nodes on the Bug Path`);

// The error node always has a Path Step, so the last one ends the path.
const pathLength = computed(() =>
  Math.max(-1, ...props.nodes.map(node => node.path_position)) + 1);

function fileName(path) {
  return typeof path === "string" ? path.split("/").pop() : "";
}

function describeLocation(location) {
  if (!location) return "";
  return `${fileName(location.file)}:${location.line}:${location.col}`;
}

// Outermost frame first.
function stackOf(frameIndex) {
  const _stack = [];
  for (let _index = frameIndex; _index !== undefined;
    _index = props.frames[_index]?.parent) {
    _stack.unshift(props.frames[_index]);
  }
  return _stack;
}

function describeCallSite(callSite) {
  return callSite ? `${fileName(callSite.file)}:${callSite.line}` : "";
}

const stepsByNode = computed(() => {
  const _byNode = new Map();
  props.steps.forEach(step => {
    const _node = step.snapshot.exploded_node;
    if (_node === undefined) return;
    if (!_byNode.has(_node)) _byNode.set(_node, []);
    _byNode.get(_node).push(step);
  });
  return _byNode;
});

// Consecutive states in one stack frame form a group headed by its calling
// context, indented by its depth, so a second call of a function reads as a
// group of its own.
const groups = computed(() => {
  const _groups = [];
  let _previous = null;
  props.nodes.forEach((node, index) => {
    const _gap = _previous === null
      ? 0 : node.path_position - _previous.path_position - 1;
    let _group = _groups[_groups.length - 1];
    if (!_group || _group.frame !== node.frame) {
      const _stack = stackOf(node.frame);
      _group = {
        key: `${index}-${node.frame}`,
        frame: node.frame,
        depth: _stack.length - 1,
        gapBefore: _gap,
        crumbs: _stack.map(frame => ({
          function: frame.function,
          callSite: describeCallSite(frame.call_site)
        })),
        contextTitle: describeStack(_stack),
        states: []
      };
      _groups.push(_group);
    }

    const _steps = [ ...(stepsByNode.value.get(index) ?? []) ]
      .sort((a, b) => a.order - b.order);
    _group.states.push({
      index,
      node,
      gapBefore: _group.states.length ? _gap : 0,
      location: describeLocation(node.location),
      steps: _steps,
      choices: props.oneChoicePerSnapshot(_steps),
      title: `Exploded graph node ${node.id}, program state ` +
        `${node.state_id}, at a ${node.kind} point` +
        (node.location ? ` on ${describeLocation(node.location)}` : "") +
        `\n${_group.contextTitle}`
    });
    _previous = node;
  });
  return _groups;
});

function describeStack(stack) {
  return [ ...stack ].reverse().map(frame => frame.call_site
    ? `in ${frame.function}, called at ${describeLocation(frame.call_site)}`
    : `in ${frame.function}`).join("\n");
}

function describeGap(count) {
  return count === 1
    ? "1 node without a Path Step"
    : `${count} nodes without a Path Step`;
}

// Of several choices the selected one is told by its place among them, since
// the panel already names it.
function stepsLabel(state) {
  if (state.choices.length === 1) return state.choices[0].label;
  const _selected = state.choices.findIndex(step =>
    step.order === props.selectedOrder);
  return _selected < 0
    ? `${state.choices.length} steps`
    : `${_selected + 1} of ${state.choices.length} steps`;
}

const selectedState = computed(() => {
  if (props.selectedOrder === null) return null;
  const _step = props.steps.find(step => step.order === props.selectedOrder);
  return _step?.snapshot.exploded_node ?? null;
});

async function scrollToSelection() {
  await nextTick();
  if (selectedState.value !== null) {
    sideView.value?.reveal(`[data-state="${selectedState.value}"]`);
  }
}

onMounted(scrollToSelection);
watch(selectedState, scrollToSelection);
</script>

<style lang="scss">
.egraph-view {
  .egraph-summary {
    white-space: nowrap;
  }

  .egraph-states {
    padding: 10px 10px 16px;
    font-size: 12px;
  }

  .egraph-frame {
    border: 1px solid #c4c8ce;
    border-radius: 4px;
    background-color: #fff;

    & + .egraph-frame,
    & + .egraph-gap {
      margin-top: 10px;
    }
  }

  .egraph-gap + .egraph-frame {
    margin-top: 2px;
  }

  .egraph-frame-header {
    display: flex;
    flex-wrap: wrap;
    gap: 2px 6px;
    align-items: baseline;
    padding: 2px 8px;
    border-bottom: 1px solid #e3e5e8;
    background-color: #f7f7f7;
  }

  .egraph-crumb-function {
    font-family: monospace;
    font-weight: 600;
  }

  .egraph-crumb-call {
    margin-left: 4px;
    color: rgba(0, 0, 0, 0.6);
    white-space: nowrap;
  }

  .egraph-crumb-sep {
    color: rgba(0, 0, 0, 0.45);
  }

  .egraph-state {
    display: flex;
    gap: 8px;
    align-items: baseline;
    width: 100%;
    min-width: 0;
    padding: 1px 8px;
    line-height: 1.6;

    & + .egraph-state {
      border-top: 1px solid #eef0f2;
    }
  }

  .egraph-state-id {
    flex: none;
    min-width: 3.2em;
    color: rgba(0, 0, 0, 0.55);
    font-family: monospace;
  }

  .egraph-state-kind {
    flex: none;
    // Fits CallExitEnd, so the usual kinds line their locations up.
    min-width: 11ch;
    font-family: monospace;
  }

  .egraph-state-location {
    flex: 1 1 auto;
    min-width: 0;
    overflow: hidden;
    color: rgba(0, 0, 0, 0.7);
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .egraph-state-steps {
    flex: none;
    font-weight: 600;
    white-space: nowrap;
  }

  .egraph-gap {
    padding: 0 8px;
    color: rgba(0, 0, 0, 0.5);
    font-style: italic;
    line-height: 1.6;
  }

  .egraph-gap-inner {
    border-top: 1px solid #eef0f2;
    background-color: #fafafa;
  }
}
</style>
