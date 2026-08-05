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

     Chainlit source analysis (xNt component in index-B28WSRhf.js):

     DOM structure:
       div.relative.flex.flex-col.flex-grow.overflow-y-auto   ← OUTER wrapper
         div[ref=a].flex.flex-col.flex-grow.overflow-y-auto   ← INNER scrollable ref
           ...messages...
           div[ref=i].flex-shrink-0                           ← spacer
         {l ? <button onClick={E}>ChevronDown</button> : null}  ← scroll-to-bottom btn

     Auto-scroll (in f(), triggered on every message update):
       a.current.scrollTop = a.current.scrollHeight          ← direct assignment

     Scroll-to-bottom button click (E()):
       a.current.scrollTo({top: a.current.scrollHeight, behavior:"smooth"})
       c(false)   ← hides the button immediately
       m()        ← debounce: reads scrollTop after 100ms, re-evaluates visibility

     onScroll handler (k()):
       const R = scrollTop + clientHeight >= scrollHeight - 10
       c(!R)      ← shows button when not at bottom, hides when at bottom

     Strategy:
       - Intercept direct `scrollTop =` assignments on the inner container via a
         property descriptor, blocked only when the user has scrolled up.
       - Do NOT intercept `scrollTo()` at all — let E() and k() run normally.
       - The descriptor's getter MUST return the real native value so Chainlit's
         debounce (m()) and visibility logic (k()) see accurate positions.       */

  // ── Identify the inner scroll container ──
  // It is the inner of the two overflow-y-auto.flex-grow divs.
  function getInnerScrollContainer() {
    const all = document.querySelectorAll('div.overflow-y-auto.flex-grow');
    // The inner one has no overflow-y-auto child that is also flex-grow,
    // or is simply the last one found nested inside another match.
    let inner = null;
    all.forEach(el => {
      // If another candidate contains this one, this is the inner.
      let isInner = false;
      all.forEach(other => {
        if (other !== el && other.contains(el)) isInner = true;
      });
      if (isInner) inner = el;
    });
    return inner || all[all.length - 1] || null;
  }

  let _patchedContainer = null;
  let _userScrolledUp = false; // true = user scrolled away from bottom → block auto-scroll

  function getNativeScrollTopDesc(el) {
    let proto = Object.getPrototypeOf(el);
    while (proto) {
      const desc = Object.getOwnPropertyDescriptor(proto, 'scrollTop');
      if (desc) return desc;
      proto = Object.getPrototypeOf(proto);
    }
    return null;
  }

  function patchScrollContainer(el) {
    if (_patchedContainer === el) return;
    _patchedContainer = el;

    const nativeDesc = getNativeScrollTopDesc(el);
    if (!nativeDesc) return;

    Object.defineProperty(el, 'scrollTop', {
      configurable: true,
      // Always return the real value so Chainlit's visibility logic works.
      get() {
        return nativeDesc.get.call(this);
      },
      set(v) {
        // Only block the write when the user has scrolled up AND the write
        // is trying to jump to the very bottom (Chainlit's auto-scroll).
        if (_userScrolledUp) {
          const threshold = this.scrollHeight - 50;
          if (v >= threshold && v > 0) {
            return; // Suppress Chainlit's programmatic jump-to-bottom.
          }
        }
        nativeDesc.set.call(this, v);
      }
    });
  }

  // Watch for the inner container to appear.
  const scrollPatchObserver = new MutationObserver(() => {
    const el = getInnerScrollContainer();
    if (el && el !== _patchedContainer) {
      patchScrollContainer(el);
      // Attach a scroll listener to track position.
      // Use capture:false so Chainlit's own onScroll (k) runs first.
      el.addEventListener('scroll', onContainerScroll, { passive: true });
    }
  });
  scrollPatchObserver.observe(document.body, { childList: true, subtree: true });

  function onContainerScroll() {
    const el = _patchedContainer;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 50;
    _userScrolledUp = !atBottom;
  }


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


  /* ── 3. Sidebar drag-to-resize ──
   *
   * Problem: React controls the wrapper's inline style and resets
   * --sidebar-width back to "16rem" on every re-render, so
   * style.setProperty() is immediately clobbered.
   *
   * Solution: inject a <style> tag into <head> targeting the wrapper by its
   * Tailwind class.  CSS custom properties with !important beat inline styles
   * per spec (CSS Cascading L4), so our value survives React re-renders.
   *
   *   .group\/sidebar-wrapper { --sidebar-width: Xpx !important; }
   *
   * DOM structure (confirmed from compiled bundle):
   *   div.group\/sidebar-wrapper          ← wrapper; inline style has --sidebar-width
   *     div.w-[--sidebar-width]           ← spacer (pushes main content right)
   *     div.fixed.w-[--sidebar-width]     ← visible panel
   *       div[data-sidebar="sidebar"]
   */
  const SIDEBAR_MIN = 160;   // px
  const SIDEBAR_MAX = 520;   // px
  const SIDEBAR_KEY = 'cl_sidebar_width';

  // <style> tag injected into <head> – survives React re-renders because
  // !important in an author stylesheet beats inline styles (CSS Cascade L4).
  let _styleEl = null;

  function getOrCreateStyleEl() {
    if (!_styleEl) {
      _styleEl = document.getElementById('sidebar-width-override');
      if (!_styleEl) {
        _styleEl = document.createElement('style');
        _styleEl.id = 'sidebar-width-override';
        document.head.appendChild(_styleEl);
      }
    }
    return _styleEl;
  }

  function getSidebarWrapper() {
    // getElementsByClassName uses literal token – no CSS escaping needed
    return document.getElementsByClassName('group/sidebar-wrapper')[0] || null;
  }

  // Return the sidebar's current rendered width in px.
  // getBoundingClientRect on the actual panel is the most reliable approach —
  // getComputedStyle on a custom property returns a raw string like " 16rem",
  // so parseFloat gives 16 (not 256), which puts the handle in the wrong place.
  function getCurrentWidthPx() {
    const wrapper = getSidebarWrapper();
    if (!wrapper) return 256;
    const panel = wrapper.querySelector('[data-sidebar="sidebar"]');
    if (panel) return panel.getBoundingClientRect().width || 256;
    return 256;
  }

  function applySidebarWidth(px) {
    const clamped = Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, Math.round(px)));
    // .group\/sidebar-wrapper  — \/ is the CSS escape for a literal /
    // In a JS string, \\/ produces the two-char sequence \/ at runtime.
    getOrCreateStyleEl().textContent =
      '.group\\/sidebar-wrapper{--sidebar-width:' + clamped + 'px!important}';
    try { localStorage.setItem(SIDEBAR_KEY, clamped); } catch (_) {}
    return clamped;
  }

  // Position the handle flush with the right edge of the sidebar panel.
  function positionHandle(handle) {
    const panel = document.querySelector('[data-sidebar="sidebar"]');
    if (!panel) return;
    const right = panel.getBoundingClientRect().right;
    if (right > 0) handle.style.left = (right - 3) + 'px';
  }

  function initSidebarResize() {
    if (document.getElementById('sidebar-drag-handle')) return;
    const wrapper = getSidebarWrapper();
    if (!wrapper) return;

    // Restore persisted width before first paint
    try {
      const saved = parseInt(localStorage.getItem(SIDEBAR_KEY), 10);
      if (saved >= SIDEBAR_MIN && saved <= SIDEBAR_MAX) applySidebarWidth(saved);
    } catch (_) {}

    const handle = document.createElement('div');
    handle.id = 'sidebar-drag-handle';
    document.body.appendChild(handle);
    // Poll every 50ms until the panel has a non-zero right edge, then stop.
    const initInterval = setInterval(() => {
      const p = document.querySelector('[data-sidebar="sidebar"]');
      if (p && p.getBoundingClientRect().right > 0) {
        positionHandle(handle);
        clearInterval(initInterval);
      }
    }, 50);

    let startX = 0;
    let startW = 0;
    let dragging = false;

    // ResizeObserver keeps the handle in sync when sidebar toggles open/close.
    // We pause it during drag to avoid it fighting with onMove.
    const ro = new ResizeObserver(() => {
      if (!dragging) positionHandle(handle);
    });
    ro.observe(wrapper);

    handle.addEventListener('mousedown', function (e) {
      e.preventDefault();
      dragging = true;
      startX = e.clientX;
      startW = getCurrentWidthPx();
      handle.classList.add('dragging');
      document.body.classList.add('sidebar-dragging');   // kills CSS transitions
      document.body.style.userSelect = 'none';

      function onMove(ev) {
        applySidebarWidth(startW + (ev.clientX - startX));
        // Always derive handle position from the panel's actual rendered edge,
        // never from the requested width, so they never diverge.
        positionHandle(handle);
      }

      function onUp() {
        dragging = false;
        handle.classList.remove('dragging');
        document.body.classList.remove('sidebar-dragging');
        document.body.style.userSelect = '';
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
        positionHandle(handle);
      }

      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });
  }

  // Try immediately (script runs deferred — React may already be painted)
  // and also watch for future mutations in case it's not ready yet.
  function tryInit() {
    if (!document.getElementById('sidebar-drag-handle') && getSidebarWrapper()) {
      initSidebarResize();
    }
  }

  tryInit();
  setTimeout(tryInit, 500);
  setTimeout(tryInit, 1500);

  const sidebarInitObserver = new MutationObserver(tryInit);
  sidebarInitObserver.observe(document.body, { childList: true, subtree: true });

})();
