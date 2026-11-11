<template>
  <v-card
    class="side-view d-flex flex-column"
    variant="flat"
    rounded="0"
    role="region"
    :aria-label="title"
  >
    <div class="side-view-header d-flex align-center px-3 py-1">
      <span
        class="text-subtitle-2 text-no-wrap"
        :title="title"
      >
        {{ compactHeader ? shortTitle : title }}
      </span>
      <slot name="header" />
      <v-spacer />
      <v-btn
        icon="mdi-close"
        variant="text"
        size="small"
        density="compact"
        :aria-label="`Close ${title.toLowerCase()}`"
        title="Close"
        @click="emit('close')"
      />
    </div>

    <v-divider />

    <template v-if="$slots.toolbar">
      <div class="side-view-toolbar d-flex align-center px-3 text-caption">
        <slot name="toolbar" />
      </div>

      <v-divider />
    </template>

    <div ref="scroller" class="side-view-scroller">
      <slot />
    </div>
  </v-card>
</template>

<script setup>
import { ref } from "vue";
import { useDisplay } from "vuetify";

defineProps({
  title: { type: String, required: true },
  // Shown below 1280px, where the header's other content needs the room.
  shortTitle: { type: String, required: true }
});

const emit = defineEmits([ "close" ]);

const { mdAndDown: compactHeader } = useDisplay();

const scroller = ref(null);

// Centers the element matching `selector` when it is out of view, so a
// selection made elsewhere shows up here too.
function reveal(selector) {
  const _element = scroller.value?.querySelector(selector);
  if (!_element) return;
  const _box = _element.getBoundingClientRect();
  const _view = scroller.value.getBoundingClientRect();
  if (_box.top < _view.top || _box.bottom > _view.bottom) {
    scroller.value.scrollTop += _box.top - _view.top -
      (_view.height - _box.height) / 2;
  }
}

defineExpose({ reveal });
</script>

<style lang="scss">
// A view beside the source that floats over it; Report.vue places it.
.side-view {
  border: 1px solid #d8dbe0;
  box-shadow: -2px 0 8px rgba(0, 0, 0, 0.12);

  .side-view-header {
    min-height: 40px;
    background-color: #f7f7f7;
  }

  .side-view-toolbar {
    min-height: 30px;
    gap: 8px;
    color: rgba(0, 0, 0, 0.6);
  }

  .side-view-legend {
    white-space: nowrap;
  }

  .side-view-legend-swatch {
    display: inline-block;
    width: 10px;
    height: 10px;
    margin-right: 4px;
    border-radius: 2px;
    vertical-align: -1px;
  }

  .side-view-legend-path {
    border: 2px solid var(--color-report-step-message-default);
  }

  .side-view-legend-step {
    background-color: var(--color-report-step-message-default-light);
  }

  .side-view-scroller {
    flex: 1 1 0;
    min-height: 0;
    overflow: auto;
  }

  // Something that selects Path Steps: the step bubble's blue while it has
  // any, the selected line's yellow and amber bar while it holds the
  // selected one.
  .side-view-choice {
    border: 0;
    background: none;
    color: inherit;
    font: inherit;
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
}
</style>
