/**
 * sidebar_debug.js
 * Paste this into the browser console while the Chainlit app is open.
 * It will report exactly what the sidebar resize script sees.
 */
(function debugSidebar() {
  console.group('=== Sidebar Resize Debug ===');

  // 1. Find the wrapper
  const wrapper = document.getElementsByClassName('group/sidebar-wrapper')[0];
  console.log('1. group/sidebar-wrapper element:', wrapper);
  if (wrapper) {
    console.log('   inline style:', wrapper.getAttribute('style'));
    console.log('   computed --sidebar-width:', getComputedStyle(wrapper).getPropertyValue('--sidebar-width'));
    console.log('   getBoundingClientRect:', wrapper.getBoundingClientRect());
  }

  // 2. Find [data-sidebar="sidebar"]
  const panel = wrapper && wrapper.querySelector('[data-sidebar="sidebar"]');
  console.log('2. [data-sidebar="sidebar"]:', panel);
  if (panel) {
    console.log('   getBoundingClientRect:', panel.getBoundingClientRect());
    console.log('   offsetWidth:', panel.offsetWidth);
  }

  // 3. Find fixed parent of panel
  const fixed = panel && panel.closest('[class*="fixed"]');
  console.log('3. fixed parent:', fixed);
  if (fixed) {
    console.log('   classes:', fixed.className);
    console.log('   getBoundingClientRect:', fixed.getBoundingClientRect());
  }

  // 4. Check if our <style> tag exists
  const styleEl = document.getElementById('sidebar-width-override');
  console.log('4. #sidebar-width-override <style>:', styleEl);
  if (styleEl) console.log('   content:', styleEl.textContent);

  // 5. Check if drag handle exists
  const handle = document.getElementById('sidebar-drag-handle');
  console.log('5. #sidebar-drag-handle:', handle);
  if (handle) {
    console.log('   style.left:', handle.style.left);
    console.log('   getBoundingClientRect:', handle.getBoundingClientRect());
    console.log('   computed z-index:', getComputedStyle(handle).zIndex);
    console.log('   computed pointer-events:', getComputedStyle(handle).pointerEvents);
  }

  // 6. Test !important override
  console.log('6. Testing !important CSS custom property override...');
  const testStyle = document.createElement('style');
  testStyle.textContent = '.group\\/sidebar-wrapper{--sidebar-width:400px!important}';
  document.head.appendChild(testStyle);
  if (wrapper) {
    const after = getComputedStyle(wrapper).getPropertyValue('--sidebar-width');
    console.log('   After injecting 400px !important, computed --sidebar-width:', after);
    console.log('   Panel width after:', panel && panel.getBoundingClientRect().width);
  }
  document.head.removeChild(testStyle);

  // 7. Check data-state of the sidebar (expanded/collapsed)
  const sidebarGroup = document.querySelector('[data-state]');
  const allDataState = document.querySelectorAll('[data-state]');
  console.log('7. All [data-state] elements:');
  allDataState.forEach(el => {
    if (el.getAttribute('data-state') === 'expanded' || el.getAttribute('data-state') === 'collapsed') {
      console.log('  ', el.tagName, el.className.slice(0,80), '→ data-state:', el.getAttribute('data-state'));
    }
  });

  // 8. Check what's at the right edge of the sidebar (z-index stack)
  if (panel) {
    const rect = panel.getBoundingClientRect();
    const x = rect.right - 1;
    const y = rect.top + rect.height / 2;
    const els = document.elementsFromPoint(x, y);
    console.log('8. Elements at sidebar right edge (' + x + ', ' + y + '):');
    els.slice(0, 6).forEach(el => {
      const cs = getComputedStyle(el);
      console.log('  ', el.tagName + (el.id ? '#'+el.id : ''), 
        el.className.slice(0,60),
        '| z-index:', cs.zIndex, 
        '| pointer-events:', cs.pointerEvents,
        '| position:', cs.position);
    });
  }

  console.groupEnd();
})();
