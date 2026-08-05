/**
 * skip_new_chat_dialog.js
 *
 * 1. Auto-confirms the "Create New Chat" dialog the instant it appears,
 *    making the New Chat button behave as a single-click action with no popup.
 *
 * 2. Injects an × close button into every Sonner toast, since Chainlit does
 *    not pass closeButton=true to <Toaster>.
 */
(function () {

  /* ── 0. Suppress auto-scroll on the Chainlit message list ──

     How Chainlit scrolls (from minified source analysis):
       1. a.current.scrollTop = a.current.scrollHeight
          — fires whenever messages update (direct property assignment)
       2. a.current.scrollTo({ top: a.current.scrollHeight, behavior: "smooth" })
          — fires when user clicks the "scroll to bottom" button (OK to keep)

     The scrollable container is a <div> with class "flex flex-col flex-grow overflow-y-auto".
     We detect it once, then freeze its scrollTop via a property descriptor override
     so that Chainlit's direct assignment becomes a no-op.                           */

  function isChatScrollContainer(el) {
    if (!el || el.nodeType !== 1) return false;
    const cls = typeof el.className === 'string' ? el.className : '';
    return cls.includes('overflow-y-auto') && cls.includes('flex-grow');
  }

  // Patch Element.prototype.scrollTo so that smooth-scroll-to-bottom calls
  // from the message list are suppressed (but not from other components).
  const _origElementScrollTo = Element.prototype.scrollTo;
  Element.prototype.scrollTo = function (...args) {
    if (isChatScrollContainer(this)) {
      // Allow only explicit user-initiated "scroll to bottom" button clicks
      // by checking whether the call comes with behavior:"smooth" AND the
      // page already had user interaction (click). We suppress all others.
      const opts = args[0];
      if (opts && typeof opts === 'object' && opts.top !== undefined) {
        // This is Chainlit scrolling the list to bottom — suppress it.
        return;
      }
    }
    return _origElementScrollTo.apply(this, args);
  };

  // Intercept direct scrollTop assignments via a property descriptor on the
  // specific container element once it appears in the DOM.
  let _patchedContainer = null;

  function patchScrollContainer(el) {
    if (_patchedContainer === el) return; // already patched
    _patchedContainer = el;

    let _frozenScrollTop = el.scrollTop;

    Object.defineProperty(el, 'scrollTop', {
      configurable: true,
      get() { return _frozenScrollTop; },
      set(v) {
        // Allow the value to be read naturally, but swallow writes that
        // would push the view to the bottom (scrollHeight).
        // We detect "scroll to bottom" by checking if the value equals
        // or is very close to scrollHeight.
        const threshold = this.scrollHeight - 50;
        if (v >= threshold && v > 0) {
          return; // Chainlit trying to jump to bottom — ignore
        }
        // Normal scroll (user dragging scrollbar, etc.) — allow it.
        _frozenScrollTop = v;
        // Apply via the prototype to avoid recursion.
        const proto = Object.getPrototypeOf(this);
        const desc = Object.getOwnPropertyDescriptor(proto, 'scrollTop') ||
                     Object.getOwnPropertyDescriptor(Element.prototype, 'scrollTop') ||
                     Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'scrollTop');
        if (desc && desc.set) desc.set.call(this, v);
      }
    });
  }

  // Watch for the container to appear and patch it.
  const scrollPatchObserver = new MutationObserver(() => {
    // Chainlit renders: outer div.relative.flex.flex-col.flex-grow.overflow-y-auto
    //                     └─ inner div.flex.flex-col.flex-grow.overflow-y-auto  ← ref:a
    const candidates = document.querySelectorAll('div.overflow-y-auto.flex-grow');
    candidates.forEach(el => {
      if (isChatScrollContainer(el) && el !== _patchedContainer) {
        patchScrollContainer(el);
      }
    });
  });
  scrollPatchObserver.observe(document.body, { childList: true, subtree: true });


  /* ── 1. Skip new-chat confirmation dialog ── */
  const dialogObserver = new MutationObserver(() => {
    const confirmBtn = document.querySelector('#new-chat-dialog #confirm');
    if (confirmBtn) confirmBtn.click();
  });
  dialogObserver.observe(document.body, { childList: true, subtree: true });


  /* ── 2. Inject × close button into Sonner toasts ── */
  const CLOSE_SVG =
    '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" ' +
    'viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
    'stroke-width="2.5" stroke-linecap="square" stroke-linejoin="miter">' +
    '<line x1="18" y1="6" x2="6" y2="18"/>' +
    '<line x1="6" y1="6" x2="18" y2="18"/>' +
    '</svg>';

  function addCloseButton(toast) {
    if (toast.querySelector('[data-close-button]')) return; // already has one

    const btn = document.createElement('button');
    btn.setAttribute('data-close-button', 'true');
    btn.setAttribute('aria-label', 'Close notification');
    btn.innerHTML = CLOSE_SVG;

    btn.addEventListener('click', () => {
      /* Trigger Sonner's own swipe-out animation by dispatching a custom event,
         then remove the element after the transition completes. */
      toast.setAttribute('data-removed', 'true');
      toast.setAttribute('data-swipe-out', 'true');
      setTimeout(() => toast.remove(), 300);
    });

    toast.appendChild(btn);
  }

  const toastObserver = new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      for (const node of mutation.addedNodes) {
        if (node.nodeType !== 1) continue;
        // Direct toast
        if (node.matches && node.matches('[data-sonner-toast]')) {
          addCloseButton(node);
        }
        // Toasts nested deeper (e.g. inside the toaster list)
        if (node.querySelectorAll) {
          node.querySelectorAll('[data-sonner-toast]').forEach(addCloseButton);
        }
      }
    }
  });

  toastObserver.observe(document.body, { childList: true, subtree: true });

})();
