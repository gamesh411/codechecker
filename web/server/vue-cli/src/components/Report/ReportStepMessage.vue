<template>
  <v-card
    size="small"
    variant="flat"
    class="report-step-msg px-2"
    :color="color"
    :step-id="id"
    :type="type"
    :style="{
      '--step-indent': marginLeft || '0px',
      '--step-gutter': gutterWidth,
      color: textColor
    }"
    rounded="xs"
    @click="selectStep"
  >
    <v-card-text class="pa-0 d-flex align-center">
      <report-step-enum-icon
        class="report-step-enum mr-1"
        :type="type"
        :index="index"
      />

      <v-btn
        v-if="prevStep"
        variant="text"
        icon="mdi-chevron-left"
        size="small"
        density="compact"
        @click="showPrevReport"
      />

      <div class="flex-grow-1 flex-shrink-1 mx-3" style="min-width: 0;">
        <div class="text-body-2 text-wrap text-break">
          {{ value }}
        </div>
        <div
          v-if="!hideDocUrl && type === 'error'"
          class="text-caption text-wrap"
        >
          <p v-if="docUrl" class="mb-0 text-caption">
            For more information see the
            <a
              class="show-documentation-btn text-primary"
              @click="showDocumentation"
            >
              checker documentation
            </a>.
          </p>
          <p
            v-else
            class="no-documentation-msg-text mb-0 text-caption text-grey"
          >
            No documentation for checker.
          </p>
        </div>
      </div>

      <v-btn
        class="report-step-copy-btn"
        variant="text"
        :icon="isCopied ? 'mdi-check' : 'mdi-content-copy'"
        size="small"
        density="compact"
        :color="isCopied ? 'green' : undefined"
        :aria-label="isCopied ? 'Copied message' : 'Copy message'"
        :title="isCopied ? 'Copied' : 'Copy message'"
        @click="copyToClipboard"
      />

      <v-btn
        v-if="nextStep"
        right
        variant="text"
        icon="mdi-chevron-right"
        size="small"
        density="compact"
        @click="showNextReport"
      />
    </v-card-text>
  </v-card>
</template>

<script setup>
import { ReportStepEnumIcon } from "@/components/Icons";
import { getCssColor } from "@/utilities/colors";
import { computed, ref } from "vue";

const props = defineProps({
  id: { type: [ String, Number ], required: true },
  value: { type: String, required: true },
  marginLeft: { type: String, default: "" },
  // The width of the source view's gutter, which the bubble's room excludes.
  gutterWidth: { type: String, default: "0px" },
  type: { type: String, default: null },
  index: { type: [ Number, String ], default: null },
  bus: { type: Object, default: null },
  prevStep: { type: Object, default: null },
  nextStep: { type: Object, default: null },
  docUrl: { type: String, default: null },
  hideDocUrl: { type: Boolean, default: false }
});

const color = computed(() => {
  switch (props.type) {
  case "error":
    return getCssColor("color-report-step-message-error-light");
  case "fixit":
    return getCssColor("color-report-step-message-fixit-light");
  case "macro":
    return getCssColor("color-report-step-message-macro-light");
  case "note":
    return getCssColor("color-report-step-message-note-light");
  default:
    return getCssColor("color-report-step-message-default-light");
  }
});

const textColor = computed(() => {
  switch (props.type) {
  case "error":
    return getCssColor("color-report-step-message-error");
  case "fixit":
    return getCssColor("color-report-step-message-fixit");
  case "macro":
    return getCssColor("color-report-step-message-macro");
  case "note":
    return getCssColor("color-report-step-message-note");
  default:
    return getCssColor("color-report-step-message-default");
  }
});

// Clicks on the step's own buttons and links must not count as selecting it.
function selectStep(event) {
  if (props.bus && !event.target.closest("button, a"))
    props.bus.emit("stepClicked", props.id);
}

function showPrevReport() {
  if (props.prevStep && props.bus)
    props.bus.emit("jpmToPrevReport", props.prevStep);
}

function showNextReport() {
  if (props.nextStep && props.bus)
    props.bus.emit("jpmToNextReport", props.nextStep);
}

function showDocumentation() {
  window.open(props.docUrl, "_blank");
}

const isCopied = ref(false);

async function copyToClipboard() {
  try {
    await navigator.clipboard.writeText(props.value);
    isCopied.value = true;

    // Reset the icon back to the copy glyph after a short delay.
    setTimeout(() => {
      isCopied.value = false;
    }, 2000);
  } catch (error) {
    console.warn("Clipboard copy failed: ", error);
  }
}
</script>

<style lang="scss">
.report-step-msg {
  // The width the source view shows right of its gutter: a bubble that runs
  // past it is cut by whatever is beside the source, such as the constraint
  // panel. The source view is the container.
  --step-room: calc(100cqw - var(--step-gutter, 0px) - 12px);
  // Indented to its event's column, but never so far that the text, beside
  // the step's number and buttons, has less than about 260px.
  --step-margin: max(0px,
    min(var(--step-indent, 0px), calc(var(--step-room) - 400px)));
  padding-top: 10px !important;
  padding-bottom: 10px !important;
  margin-bottom: 2px;
  margin-left: var(--step-margin);
  max-width: min(640px, calc(var(--step-room) - var(--step-margin)));
  white-space: inherit;
  height: auto !important;
  border-radius: 12px !important;
  align-items: center !important;

  &.v-size--small {
    min-height: 24px;
    height: auto;

    .v-chip.report-step-enum {
      overflow: inherit;
    }

    .show-documentation-btn {
      border-bottom: 1px dashed var(--color-soft-blue);
      display: inline-block;
    }
  }
  .no-documentation-msg-text {
    color: grey
  }

  .report-step-copy-btn {
    opacity: 0;
    transition: opacity 0.15s ease-in-out;
  }

  &:hover .report-step-copy-btn,
  &:focus-within .report-step-copy-btn {
    opacity: 1;
  }
}

.current {
  border: 2px dashed rgb(var(--v-theme-primary)) !important;
  opacity: 1;
  font-weight: bold;
}

.current[type="error"] {
  border: 2px dashed var(--color-report-step-message-error) !important;
}

.current[type="fixit"] {
  border: 2px dashed var(--color-report-step-message-fixit) !important;
}

.current[type="macro"] {
  border: 2px dashed var(--color-report-step-message-macro) !important;
}

.current[type="note"] {
  border: 2px dashed var(--color-report-step-message-macro) !important;
}
</style>
