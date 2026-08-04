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
