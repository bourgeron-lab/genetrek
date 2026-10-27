/**
 * Token editor for search input.
 * Expects global `window._tokenEditorConfig` to be set before this script runs:
 *   { hiddenTextareaId, tokenColors }
 */
(function() {
    function initTokenEditor() {
        const cfg = window._tokenEditorConfig;
        if (!cfg) { setTimeout(initTokenEditor, 100); return; }

        const hiddenTextarea = document.getElementById(cfg.hiddenTextareaId);
        const editorBox = document.getElementById('token-editor-box');
        const tokenInput = document.getElementById('token-input-field');

        if (!editorBox || !tokenInput) { return; }

        const delimiterRegex = /[,;\s]+/;
        // Thousands separators inside a locus, stripped before the split below.
        // UCSC -- which the gene dialog links out to -- prints
        // chr22:50,674,408-50,733,212, so pasting one back is an obvious move,
        // and splitting on its commas first would scatter it across five
        // meaningless badges. Mirrors _COMMA_LOCUS_RE in services/search.py,
        // which is what actually decides the query; this keeps the badges
        // honest about what is being searched. The span cannot end on a comma,
        // or the strip would swallow whatever token follows.
        const commaLocusRegex =
            /(?:chr)?(?:\d+|X|Y|MT|M):\d(?:[\d,]*\d)?-\d(?:[\d,]*\d)?/gi;
        const tokenColors = cfg.tokenColors;

        function syncToHidden() {
            const badges = editorBox.querySelectorAll('.search-token-badge');
            const tokens = Array.from(badges).map(b => b.dataset.value);
            if (hiddenTextarea) {
                hiddenTextarea.value = tokens.join(',');
                hiddenTextarea.dispatchEvent(new Event('input', { bubbles: true }));
            }
        }

        function categorizeToken(token) {
            // Before the locus rule: HGNC:14294 is an identifier, not an interval.
            if (/^HGNC:\d+$/i.test(token)) return 'hgnc';
            if (token.includes(':')) return 'loci';
            if (/^ENSG\d{11}/.test(token)) return 'ensembl';
            if (/^\d+$/.test(token)) return 'ncbi';
            return 'symbol';
        }

        function createBadge(token) {
            const tokenType = categorizeToken(token);
            const backgroundColor = tokenColors[tokenType];

            const span = document.createElement('span');
            span.className = 'search-token-badge';
            span.style.backgroundColor = backgroundColor;
            span.dataset.value = token;
            span.dataset.type = tokenType;

            const text = document.createElement('span');
            text.className = 'search-token-text';
            text.textContent = token;
            span.appendChild(text);

            const remove = document.createElement('span');
            remove.className = 'search-token-remove';
            remove.innerHTML = '&times;';
            remove.title = 'Remove';
            remove.onclick = function(e) {
                e.stopPropagation();
                span.remove();
                syncToHidden();
                tokenInput.focus();
            };
            span.appendChild(remove);

            editorBox.insertBefore(span, tokenInput);
            syncToHidden();
        }

        function addTokensFromInput() {
            const raw = tokenInput.value;
            if (!raw) return;
            const joined = raw.replace(commaLocusRegex, function(m) {
                return m.replace(/,/g, '');
            });
            const parts = joined.split(delimiterRegex);
            parts.forEach(function(t) {
                t = t.replace(/["'`]/g, '').trim();
                if (t) createBadge(t);
            });
            tokenInput.value = '';
        }

        tokenInput.addEventListener('keydown', function(e) {
            if (e.key === 'Backspace' && tokenInput.value === '') {
                const badges = editorBox.querySelectorAll('.search-token-badge');
                if (badges.length) {
                    badges[badges.length - 1].remove();
                    syncToHidden();
                }
                e.preventDefault();
                return;
            }
            if (e.key === 'Enter' || e.key === 'Tab') {
                if (tokenInput.value.trim()) {
                    e.preventDefault();
                    addTokensFromInput();
                }
            }
        });

        tokenInput.addEventListener('input', function() {
            const val = tokenInput.value;
            if (val.match(/[,;\s]/)) {
                setTimeout(addTokensFromInput, 0);
            }
        });

        tokenInput.addEventListener('paste', function() {
            setTimeout(addTokensFromInput, 50);
        });

        editorBox.addEventListener('click', function() {
            tokenInput.focus();
        });

        // Sync from hidden textarea on clear
        if (hiddenTextarea) {
            const observer = new MutationObserver(function() {
                if (hiddenTextarea.value === '') {
                    editorBox.querySelectorAll('.search-token-badge').forEach(b => b.remove());
                }
            });
            observer.observe(hiddenTextarea, { attributes: true, childList: true, subtree: true });
            setInterval(function() {
                if (hiddenTextarea.value === '' && editorBox.querySelectorAll('.search-token-badge').length > 0) {
                    editorBox.querySelectorAll('.search-token-badge').forEach(b => b.remove());
                }
            }, 200);
        }

        window.triggerTokenize = function() {
            addTokensFromInput();
        };

        // Replace the badges with a search that arrived in the URL
        // (?search= / #search=), so the drawer shows what was searched.
        window.setSearchTokens = function(text) {
            editorBox.querySelectorAll('.search-token-badge').forEach(b => b.remove());
            tokenInput.value = text || '';
            addTokensFromInput();
        };
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initTokenEditor);
    } else {
        initTokenEditor();
    }
})();
