/**
 * My lists: the browser half of user gene lists.
 *
 * The only code that touches localStorage or the URL fragment. Lists live
 * here and nowhere else -- the server receives them to compute while the tab
 * is open, keeps them in that connection's memory, and never stores them.
 *
 * Python owns the content: it builds every record (services/user_lists.py)
 * and calls put/remove; this file only persists, announces and copies.
 *
 * Events emitted to Python:
 *   userListsLoaded  {lists, part, parts, timezone, base, error}
 *                    -- the whole store, in parts that each fit one message
 *   sharedLists      {payload}        -- a #lists= token
 *   userListFile     {upload, name, text, part, parts}
 *                    -- a file the user chose to import, in chunks
 *   userListsError   {message}        -- a failed write or unreadable file
 *
 * Files are read here, in the browser, and their text sent over the page's
 * websocket -- never as an HTTP upload, which the server would spool to disk
 * above 1 MiB. The websocket drops any message over 1,000,000 bytes, so each
 * part of the store and each chunk of a file stays under MAX_MESSAGE_BYTES.
 * The three limits below are the same numbers as services/user_lists.py.
 */
(function () {
    var KEY = 'genetrek.my_lists';
    var FORMAT = 1;
    var SHARE_RE = /(?:^#|&)lists=([A-Za-z0-9_-]+)/;
    var MAX_MESSAGE_BYTES = 900000;
    var MAX_IMPORT_BYTES = 8000000;
    var IMPORT_CHUNK_CHARS = 250000;
    var sharedChecked = false;

    function emit(name, args) {
        (function attempt(n) {
            if (typeof emitEvent === 'function') {
                emitEvent(name, args);
            } else if (n < 50) {
                setTimeout(function () { attempt(n + 1); }, 200);
            }
        })(0);
    }

    function read() {
        var raw;
        try {
            raw = window.localStorage.getItem(KEY);
        } catch (err) {
            // Storage disabled (some private modes, strict privacy settings).
            return { format: FORMAT, lists: [], error: 'unavailable' };
        }
        if (!raw) return { format: FORMAT, lists: [] };
        try {
            var store = JSON.parse(raw);
            if (store && Array.isArray(store.lists)) return store;
        } catch (err) { /* fall through */ }
        return { format: FORMAT, lists: [], error: 'damaged' };
    }

    function write(store) {
        try {
            window.localStorage.setItem(KEY, JSON.stringify(store));
            return true;
        } catch (err) {
            var quota = err && (err.name === 'QuotaExceededError' || err.code === 22);
            emit('userListsError', {
                message: quota
                    ? 'Your browser has no room left for GeneTrek lists. Export and delete some.'
                    : 'Your browser refused to save the list (storage disabled?).',
            });
            return false;
        }
    }

    function base() {
        return window.location.origin + window.location.pathname;
    }

    function timezone() {
        try {
            return Intl.DateTimeFormat().resolvedOptions().timeZone || '';
        } catch (err) {
            return '';
        }
    }

    function byteLength(text) {
        return new Blob([text]).size;
    }

    /** Split records into parts whose JSON each fits one websocket message. */
    function parts(records) {
        var out = [[]];
        var size = 0;
        var budget = MAX_MESSAGE_BYTES - 2000;
        records.forEach(function (rec) {
            var n = byteLength(JSON.stringify(rec)) + 1;
            if (n > budget) return;  // one record alone cannot be sent
            if (size + n > budget && out[out.length - 1].length) {
                out.push([]);
                size = 0;
            }
            out[out.length - 1].push(rec);
            size += n;
        });
        return out;
    }

    function announce() {
        var store = read();
        var chunks = parts(store.lists);
        chunks.forEach(function (chunk, index) {
            emit('userListsLoaded', {
                lists: chunk,
                part: index,
                parts: chunks.length,
                format: store.format,
                timezone: timezone(),
                base: base(),
                error: store.error || null,
            });
        });
        if (!sharedChecked) {
            sharedChecked = true;
            var match = window.location.hash.match(SHARE_RE);
            if (match) emit('sharedLists', { payload: match[1] });
        }
    }

    function pickFile() {
        var input = document.createElement('input');
        input.type = 'file';
        input.accept = '.json,.txt,.tsv,.csv,.tab,text/plain,application/json';
        input.addEventListener('change', function () {
            var file = input.files && input.files[0];
            if (!file) return;
            if (file.size > MAX_IMPORT_BYTES) {
                emit('userListsError', {
                    message: file.name + ' is larger than '
                        + Math.floor(MAX_IMPORT_BYTES / 1000000) + ' MB; split it into smaller files.',
                });
                return;
            }
            var reader = new FileReader();
            reader.onload = function () {
                var text = String(reader.result || '');
                var upload = Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
                var parts = Math.max(1, Math.ceil(text.length / IMPORT_CHUNK_CHARS));
                for (var i = 0; i < parts; i++) {
                    emit('userListFile', {
                        upload: upload,
                        name: file.name,
                        text: text.slice(i * IMPORT_CHUNK_CHARS, (i + 1) * IMPORT_CHUNK_CHARS),
                        part: i,
                        parts: parts,
                    });
                }
            };
            reader.onerror = function () {
                emit('userListsError', { message: 'Could not read ' + file.name + '.' });
            };
            reader.readAsText(file);
        });
        input.click();
    }

    window.gtLists = {
        announce: announce,
        // Called from a click handler that runs in the browser: a file picker
        // opened after a server round trip loses the user gesture and is
        // blocked.
        pickFile: pickFile,

        /** Insert or replace records by id. Damaged stores are not overwritten. */
        put: function (records) {
            var store = read();
            if (store.error === 'damaged' || store.error === 'unavailable') {
                emit('userListsError', {
                    message: store.error === 'damaged'
                        ? 'The saved lists in this browser are damaged; nothing was overwritten.'
                        : 'This browser does not allow GeneTrek to store lists.',
                });
                return false;
            }
            var byId = {};
            var order = [];
            store.lists.forEach(function (rec) {
                if (rec && rec.id && !(rec.id in byId)) order.push(rec.id);
                if (rec && rec.id) byId[rec.id] = rec;
            });
            records.forEach(function (rec) {
                if (!(rec.id in byId)) order.push(rec.id);
                byId[rec.id] = rec;
            });
            store.lists = order.map(function (id) { return byId[id]; });
            store.format = store.format || FORMAT;
            return write(store);
        },

        remove: function (ids) {
            var store = read();
            if (store.error) return false;
            var drop = {};
            ids.forEach(function (id) { drop[id] = true; });
            store.lists = store.lists.filter(function (rec) { return !(rec && drop[rec.id]); });
            return write(store);
        },

        /** Forget the share token so a reload does not offer it again. */
        clearShared: function () {
            if (SHARE_RE.test(window.location.hash)) {
                window.history.replaceState(
                    null, '', window.location.pathname + window.location.search,
                );
            }
        },

        copy: function (text) {
            if (navigator.clipboard) {
                navigator.clipboard.writeText(text);
                return true;
            }
            console.error('Clipboard API needs a secure context (HTTPS or localhost).');
            return false;
        },
    };

    // Another tab saved or deleted a list: this tab re-reads the store, so
    // the two never disagree about what exists.
    window.addEventListener('storage', function (event) {
        if (event.key === KEY || event.key === null) announce();
    });
})();
