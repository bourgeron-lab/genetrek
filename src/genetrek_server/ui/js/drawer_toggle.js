/**
 * Drawer toggle positioning logic.
 * Expects global `window._drawerToggleConfig` to be set before this script runs:
 *   { leftToggleId, leftHiddenBtnId, rightToggleId, rightHiddenBtnId }
 */
(function() {
    const cfg = window._drawerToggleConfig;
    if (!cfg) return;

    // --- Left drawer toggle ---
    function updateSearchArrows() {
        const drawer = document.querySelector('.q-drawer--left');
        const arrowTop = document.getElementById('search-arrow-top');
        const arrowBottom = document.getElementById('search-arrow-bottom');
        if (!drawer || !arrowTop || !arrowBottom) return;

        const isOpen = drawer.getBoundingClientRect().right > 0;
        const pathLeft = 'M15.41 16.59L10.83 12l4.58-4.59L14 6l-6 6 6 6 1.41-1.41z';
        const pathRight = 'M8.59 16.59L13.17 12 8.59 7.41 10 6l6 6-6 6-1.41-1.41z';
        arrowTop.querySelector('path').setAttribute('d', isOpen ? pathLeft : pathRight);
        arrowBottom.querySelector('path').setAttribute('d', isOpen ? pathLeft : pathRight);
    }

    function positionLeftToggle() {
        const toggle = document.getElementById(cfg.leftToggleId);
        const drawer = document.querySelector('.q-drawer--left');
        if (!toggle) return;

        let left = 0;
        if (drawer) {
            const rect = drawer.getBoundingClientRect();
            if (rect.right > 0) left = rect.right;
        }
        toggle.style.setProperty('left', `${left}px`, 'important');
        updateSearchArrows();
    }

    // --- Right drawer toggle ---
    function updateStatsArrows() {
        const drawer = document.querySelector('.q-drawer--right');
        const arrowTop = document.getElementById('stats-arrow-top');
        const arrowBottom = document.getElementById('stats-arrow-bottom');
        if (!drawer || !arrowTop || !arrowBottom) return;

        const viewportWidth = document.documentElement.clientWidth;
        const isOpen = drawer.getBoundingClientRect().left < viewportWidth;
        const pathLeft = 'M15.41 16.59L10.83 12l4.58-4.59L14 6l-6 6 6 6 1.41-1.41z';
        const pathRight = 'M8.59 16.59L13.17 12 8.59 7.41 10 6l6 6-6 6-1.41-1.41z';
        arrowTop.querySelector('path').setAttribute('d', isOpen ? pathRight : pathLeft);
        arrowBottom.querySelector('path').setAttribute('d', isOpen ? pathRight : pathLeft);
    }

    function positionRightToggle() {
        const toggle = document.getElementById(cfg.rightToggleId);
        const drawer = document.querySelector('.q-drawer--right');
        if (!toggle) return;

        const viewportWidth = document.documentElement.clientWidth;
        let right = 0;
        if (drawer) {
            const rect = drawer.getBoundingClientRect();
            if (rect.left < viewportWidth) right = viewportWidth - rect.left;
        }
        toggle.style.setProperty('right', `${right}px`, 'important');
        updateStatsArrows();
    }

    // --- Shared animation helpers ---
    function createAnimator(positionFn) {
        let animId = null;
        return function startAnimation() {
            if (animId) cancelAnimationFrame(animId);
            const startTime = performance.now();
            const duration = 350;
            function animate() {
                positionFn();
                if (performance.now() - startTime < duration) {
                    animId = requestAnimationFrame(animate);
                }
            }
            animId = requestAnimationFrame(animate);
        };
    }

    const animateLeft = createAnimator(positionLeftToggle);
    const animateRight = createAnimator(positionRightToggle);

    const leftToggle = document.getElementById(cfg.leftToggleId);
    if (leftToggle) leftToggle.addEventListener('click', animateLeft);

    const rightToggle = document.getElementById(cfg.rightToggleId);
    if (rightToggle) rightToggle.addEventListener('click', animateRight);

    // Watch for drawer changes
    const observer = new MutationObserver(function() {
        animateLeft();
        animateRight();
    });
    const layout = document.querySelector('.q-layout');
    if (layout) {
        observer.observe(layout, { attributes: true, subtree: true, childList: true });
    }

    window.addEventListener('resize', function() {
        positionLeftToggle();
        positionRightToggle();
    });

    // The animator above tracks the drawer for a fixed *wall-clock* window, so
    // anything that blocks the main thread mid-slide consumes the budget before
    // the drawer has finished moving -- and the toggle is left at the edge it
    // started from, with nothing to correct it. The statistics drawer builds
    // its plots on first open and does exactly that on the full gene set.
    //
    // transitionend is the authoritative final position: it fires when the
    // drawer has actually stopped, however long the slide really took. Bound on
    // the document because it bubbles and the drawers may not exist yet when
    // this script runs.
    document.addEventListener('transitionend', function(event) {
        const target = event.target;
        if (target instanceof Element && target.classList.contains('q-drawer')) {
            positionLeftToggle();
            positionRightToggle();
        }
    });

    // Position on load with retries
    function positionAll() { positionLeftToggle(); positionRightToggle(); }
    positionAll();
    requestAnimationFrame(positionAll);
    setTimeout(positionAll, 0);
    setTimeout(positionAll, 50);
    setTimeout(positionAll, 100);
    setTimeout(positionAll, 200);
    setTimeout(positionAll, 500);
    window.addEventListener('DOMContentLoaded', positionAll);
    window.addEventListener('load', positionAll);
})();
