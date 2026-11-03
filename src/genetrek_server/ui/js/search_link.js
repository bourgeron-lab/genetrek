/**
 * Search links: run a search carried in the URL fragment.
 *
 * #search=SHANK3,chr22:50000000-51000000&genome=GRCh38 does what
 * ?search=...&genome=... does, but the fragment never reaches a server, so
 * a list of genes or a patient's locus stays out of the access logs (see
 * "Gene lists reach the API by POST only" in CLAUDE.md).
 *
 * Emits to Python:
 *   searchLink  {search, genome}
 *
 * The two keys are then removed from the fragment, so a reload does not run
 * the search again; anything else in it (a #lists= share) is left alone.
 */
(function () {
    var SEARCH_RE = /(?:^#|&)search=([^&]*)/;
    var GENOME_RE = /(?:^#|&)genome=([^&]*)/;

    function decode(value) {
        try {
            return decodeURIComponent(value.replace(/\+/g, ' '));
        } catch (err) {
            return '';
        }
    }

    function emit(name, args) {
        (function attempt(n) {
            if (typeof emitEvent === 'function') {
                emitEvent(name, args);
            } else if (n < 50) {
                setTimeout(function () { attempt(n + 1); }, 200);
            }
        })(0);
    }

    var hash = window.location.hash;
    var match = hash.match(SEARCH_RE);
    if (!match) return;
    var genome = hash.match(GENOME_RE);
    emit('searchLink', {
        search: decode(match[1]),
        genome: genome ? decode(genome[1]) : null,
    });

    var rest = hash.replace(/^#/, '').split('&').filter(function (part) {
        return part && !/^(search|genome)=/.test(part);
    }).join('&');
    window.history.replaceState(
        null, '', window.location.pathname + window.location.search + (rest ? '#' + rest : ''),
    );
})();
