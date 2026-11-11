import _ from "lodash";

function fillHeight(el) {
  el.style.overflow = "auto";
  const windowHeight = window.innerHeight;
  // Relative to the document: measured while the page is scrolled, the
  // viewport top would make the element taller by the scroll offset, and
  // that height keeps the page scrollable by the same amount.
  const top = el.getBoundingClientRect().top + window.scrollY;

  // Get footer element height.
  let footerHeight = 0;
  const footerElement = document.querySelector("footer");
  if (footerElement) {
    const footerStyle = window.getComputedStyle(footerElement);
    footerHeight = parseFloat(footerStyle.height);
  }

  let current = el;
  let style = null;
  let paddingBottom = 0;
  while (current.parentNode && current.parentNode !== document) {
    current = current.parentNode;

    style = window.getComputedStyle(current);
    paddingBottom += parseInt(style.paddingBottom, 10);
  }

  // TODO: We have to extract -1 from the heigh to hide scrollbar. Find out
  // a better solution for this problem.
  el.style.height =
    Math.floor(windowHeight - top - paddingBottom - footerHeight - 2) + "px";
  return el.style.height;
}

export const FillHeight = {
  mounted(el, binding) {
    const callback = binding.value || (() => {});
    const options = binding.options || {
      passive: true
    };

    const fn = () => {
      const height = fillHeight(el);
      callback(el, height);
    };

    window.addEventListener("resize", _.debounce(fn, 200), options);

    // Content above the element can change height after mount, such as a
    // header that wraps once a child component loads. The page then grows
    // past the window, so the body's size is what tells us to measure again.
    // Once the element fits again the body is back to the same size, so this
    // settles after one round.
    el._bodyObserver = new ResizeObserver(() => fn());
    el._bodyObserver.observe(document.body);

    el._onResize = { fn, options };
    
    // Call immediately after mount
    fn(el);
  },

  updated(el) {
    if (!el._onResize) return;

    const { fn } = el._onResize;
    fn(el);
  },

  unmounted(el) {
    el._bodyObserver?.disconnect();
    delete el._bodyObserver;

    if (!el._onResize) return;
    const { fn, options } = el._onResize;

    window.removeEventListener("resize", fn, options);
    delete el._onResize;
  }
};

export default FillHeight;
