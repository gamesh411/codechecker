import { onBeforeUnmount, ref, watch } from "vue";

// Whether a scroll container hides content above or below its viewport, so
// the cut edge can show that there is more. `scroller` holds a single content
// element, whose size changes are what move the edges.
export function useScrollShadows(scroller) {
  const canScrollUp = ref(false);
  const canScrollDown = ref(false);

  function update() {
    const _el = scroller.value;
    if (!_el) return;
    canScrollUp.value = _el.scrollTop > 0;
    // Fractional heights at some zoom levels leave a pixel of rounding.
    canScrollDown.value =
      _el.scrollHeight - _el.clientHeight - _el.scrollTop > 1;
  }

  const _observer = new ResizeObserver(update);

  watch(scroller, (el, oldEl) => {
    if (oldEl) {
      oldEl.removeEventListener("scroll", update);
      _observer.disconnect();
    }
    if (!el) return;
    el.addEventListener("scroll", update, { passive: true });
    _observer.observe(el);
    if (el.firstElementChild) _observer.observe(el.firstElementChild);
    update();
  }, { immediate: true, flush: "post" });

  onBeforeUnmount(() => {
    scroller.value?.removeEventListener("scroll", update);
    _observer.disconnect();
  });

  return { canScrollUp, canScrollDown };
}
