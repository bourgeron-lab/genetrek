/**
 * GeneTrek TanStack Table wrapper.
 *
 * Depends on window.TanStackTable being set (from CDN import in the module header).
 * Initialized via: new GeneTrekTable(containerId, options)
 *
 * Pagination is handled client-side (UI + state), but data is fetched from
 * the Python server via emitEvent('page-change', ...) / emitEvent('sort-change', ...).
 */
class GeneTrekTable {
    constructor(containerId, options) {
        this.container = document.getElementById(containerId);
        this.columnDefs = options.columnDefs || [];
        // Which column carries the primary key and which the gene symbol. Sent
        // from Python because only the metadata knows: this file must not name
        // an identifier column.
        this.roles = options.roles || {};
        this.data = [];
        this.sorting = [];
        this.table = null;

        // Pagination state (JS-owned, data from Python)
        this.currentPage = 1;
        this.rowsPerPage = 20;
        this.totalRows = 0;
        this.rowsPerPageOptions = [10, 20, 50, 100];

        this.init();
    }

    init() {
        var TanStack = window.TanStackTable;
        var self = this;

        this.table = TanStack.createTable({
            data: this.data,
            columns: this.buildColumns(),
            state: {
                sorting: this.sorting,
            },
            onStateChange: function(updater) {
                self.table.setOptions(function(prev) {
                    return Object.assign({}, prev, {
                        state: updater(self.table.getState()),
                    });
                });
            },
            onSortingChange: function(updater) {
                self.sorting = typeof updater === 'function' ? updater(self.sorting) : updater;
                self.table.setOptions(function(prev) {
                    return Object.assign({}, prev, {
                        state: Object.assign({}, self.table.getState(), { sorting: self.sorting }),
                    });
                });
                // Re-render immediately to update sort indicators in headers
                self.render();
                // Notify Python to re-sort full dataset and send page 1
                if (typeof emitEvent === 'function') {
                    emitEvent('sortChange', {
                        sorting: self.sorting.map(function(s) {
                            return { id: s.id, desc: s.desc };
                        })
                    });
                }
            },
            getCoreRowModel: TanStack.getCoreRowModel(),
        });

        // Seed active state with all feature defaults (columnPinning, etc.).
        // Vanilla JS createTable() does NOT auto-merge initialState like React/Vue adapters do.
        this.table.setOptions(function(prev) {
            return Object.assign({}, prev, {
                state: Object.assign({}, self.table.initialState, prev.state),
            });
        });
    }

    updateColumnsAndData(columnDefs, data, totalRows) {
        var self = this;
        this.columnDefs = columnDefs;
        this.data = data;
        if (typeof totalRows === 'number') {
            this.totalRows = totalRows;
        }
        this.table.setOptions(function(prev) {
            return Object.assign({}, prev, {
                columns: self.buildColumns(),
                data: self.data,
                state: self.table.getState(),
            });
        });
        this.render();
    }

    resetToPage(page) {
        this.currentPage = page;
        this.renderPagination();
    }

    buildColumns() {
        return this.columnDefs.map(function(def) {
            return {
                // Explicit id, not TanStack's derived one: it replaces '.' with
                // '_' in an accessorKey, and both the sort emit and the column
                // dialog send this back to Python as a parquet column name.
                id: def.id,
                accessorKey: def.id,
                header: def.header,
                meta: {
                    cellType: def.cellType,
                    badgeConfig: def.badgeConfig,
                    tooltip: def.tooltip,
                    markNull: def.markNull,
                },
            };
        });
    }

    render() {
        if (!this.container) return;
        this.container.innerHTML = '';

        if (this.data.length === 0 && this.totalRows === 0) {
            this.container.innerHTML =
                '<div style="text-align: center; padding: 40px; color: #718096;">' +
                '<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="#cbd5e0" stroke-width="2" style="margin-bottom: 16px;">' +
                '<circle cx="11" cy="11" r="8"></circle><path d="m21 21-4.3-4.3"></path></svg>' +
                '<p style="font-size: 1.25rem; font-weight: 600; margin-bottom: 8px;">No genes found</p>' +
                '<p style="font-size: 0.875rem;">Try adjusting your search criteria or filters.</p>' +
                '</div>';
            return;
        }

        var table = document.createElement('table');
        table.className = 'gene-table';

        // Render header
        var thead = document.createElement('thead');
        var headerGroups = this.table.getHeaderGroups();
        for (var gi = 0; gi < headerGroups.length; gi++) {
            var headerGroup = headerGroups[gi];
            var tr = document.createElement('tr');
            var headers = headerGroup.headers;
            for (var hi = 0; hi < headers.length; hi++) {
                var header = headers[hi];
                var th = document.createElement('th');
                var meta = header.column.columnDef.meta || {};

                th.className = 'sortable';

                var sortDir = header.column.getIsSorted();
                if (sortDir === 'asc') th.className += ' sort-asc';
                else if (sortDir === 'desc') th.className += ' sort-desc';

                th.title = meta.tooltip || '';

                // Wrapper for flex layout (text above, sort indicator below)
                var wrapper = document.createElement('div');
                wrapper.className = 'th-wrapper';

                // Vertical text label
                var textSpan = document.createElement('span');
                textSpan.className = 'th-text';
                textSpan.textContent = header.column.columnDef.header;
                wrapper.appendChild(textSpan);

                // Sort indicator below the text
                var sortSpan = document.createElement('span');
                sortSpan.className = 'th-sort-indicator';
                if (sortDir === 'asc') sortSpan.textContent = '▲';
                else if (sortDir === 'desc') sortSpan.textContent = '▼';
                else sortSpan.textContent = '⇅';
                wrapper.appendChild(sortSpan);

                th.appendChild(wrapper);

                // One listener, branching on where the click landed, rather
                // than a second listener on the label calling stopPropagation():
                // the label sits inside the sortable th, and swallowing the
                // event there would also hide the click from anything listening
                // further up the document.
                th.dataset.columnId = header.column.id;
                (function(h) {
                    th.addEventListener('click', function(e) {
                        var label = e.target.closest && e.target.closest('.th-text');
                        if (label) {
                            // The label describes the column; the rest of the
                            // header sorts it.
                            if (typeof emitEvent === 'function') {
                                emitEvent('columnInfo', {
                                    column: e.currentTarget.dataset.columnId
                                });
                            }
                            return;
                        }
                        if (h) h(e);
                    });
                })(header.column.getToggleSortingHandler());

                tr.appendChild(th);
            }
            // Action column header
            var actionTh = document.createElement('th');
            tr.appendChild(actionTh);
            thead.appendChild(tr);
        }
        table.appendChild(thead);

        // Render body
        var tbody = document.createElement('tbody');
        var rows = this.table.getRowModel().rows;
        for (var ri = 0; ri < rows.length; ri++) {
            var row = rows[ri];
            var bodyTr = document.createElement('tr');
            // The dialog is keyed off the row, not the cell, so the info button
            // and the symbol can both open it.
            if (this.roles.primaryKey) {
                var key = row.original[this.roles.primaryKey];
                if (key != null) bodyTr.dataset.geneKey = String(key);
            }
            var cells = row.getVisibleCells();
            for (var ci = 0; ci < cells.length; ci++) {
                var cell = cells[ci];
                var td = document.createElement('td');
                var html = this.renderCell(cell);
                // Keyed on the column id rather than on position, so the symbol
                // stays clickable however the columns are reordered.
                if (html && cell.column.id === this.roles.symbolColumn) {
                    html = '<span class="gene-symbol">' + html + '</span>';
                }
                td.innerHTML = html;
                bodyTr.appendChild(td);
            }
            // Action column
            var actionTd = document.createElement('td');
            actionTd.className = 'action-column';
            actionTd.innerHTML = this.renderInfoButton();
            bodyTr.appendChild(actionTd);
            tbody.appendChild(bodyTr);
        }

        // One delegated listener rather than two per row, reattached with the
        // body on every render.
        tbody.addEventListener('click', function(e) {
            var trigger = e.target.closest &&
                e.target.closest('.gene-symbol, .gene-info-btn');
            if (!trigger) return;
            var tr = trigger.closest('tr');
            if (tr && tr.dataset.geneKey && typeof emitEvent === 'function') {
                emitEvent('geneInfo', { gene: tr.dataset.geneKey });
            }
        });
        table.appendChild(tbody);

        var tableWrapper = document.createElement('div');
        tableWrapper.className = 'table-container';
        tableWrapper.appendChild(table);

        // Scroll container — provides horizontal scrollbar when table is wider than viewport
        var scrollContainer = document.createElement('div');
        scrollContainer.className = 'table-scroll-container';
        scrollContainer.appendChild(tableWrapper);
        this.container.appendChild(scrollContainer);

        // Dynamically size trapezoid headers based on longest text
        this._adjustHeaderHeight(tableWrapper);

        // Render pagination controls below the table
        this.renderPagination();
    }

    _adjustHeaderHeight(tableWrapper) {
        // Measure the longest diagonal header text to size trapezoids
        var textEls = tableWrapper.querySelectorAll(
            '.gene-table th:not(:first-child):not(:last-child) .th-text'
        );
        if (textEls.length === 0) return;

        // Measure the real label elements rather than a ruler carrying a copy
        // of their font: the CSS in styles.py is then the only place the header
        // font is defined, and font-family, weight, letter-spacing and the
        // label's padding are all accounted for by construction.
        //
        // offsetWidth, not getBoundingClientRect(): the labels are rotated -45deg
        // and the rect would be the transformed bounding box (~W/sqrt(2)), which
        // would roughly halve the measurement and collapse the headers. Read all
        // of them before writing anything below, so this costs one layout flush.
        var maxWidth = 0;
        for (var i = 0; i < textEls.length; i++) {
            if (textEls[i].offsetWidth > maxWidth) maxWidth = textEls[i].offsetWidth;
        }

        // Height = text width projected onto vertical axis at 45° + padding
        // sin(45°) ≈ 0.707; add padding for sort indicator + top spacing
        var height = Math.ceil(maxWidth * 0.707) + 30;
        if (height < 80) height = 80; // minimum height

        // Apply to all diagonal wrappers
        var wrappers = tableWrapper.querySelectorAll(
            '.gene-table th:not(:first-child):not(:last-child) .th-wrapper'
        );
        for (var j = 0; j < wrappers.length; j++) {
            wrappers[j].style.height = height + 'px';
        }

        // Set padding-top on scroll container so diagonal headers aren't clipped.
        // (Using padding-top instead of margin-top because overflow-x:auto
        //  forces overflow-y to auto too, which would clip upward overflow.)
        var scrollContainer = tableWrapper.parentElement;
        if (scrollContainer && scrollContainer.classList.contains('table-scroll-container')) {
            scrollContainer.style.paddingTop = (height - 25) + 'px';
        }

        // Add right padding to table-container so the last trapezoid's skew
        // overhang (and its text) isn't clipped by overflow-x:clip.
        // skewX(-45deg) shifts the top-right corner right by ~height/2.
        var skewPad = Math.ceil(height / 2) + 5;
        tableWrapper.style.paddingRight = skewPad + 'px';
    }

    renderPagination() {
        // Remove existing pagination if present
        var existingPag = document.getElementById('genetrek-pagination');
        if (existingPag) existingPag.remove();

        var paginationEl = document.createElement('div');
        paginationEl.id = 'genetrek-pagination';
        paginationEl.className = 'genetrek-pagination';

        var self = this;
        var totalPages = Math.max(1, Math.ceil(this.totalRows / this.rowsPerPage));

        // First
        var firstBtn = this._createPaginationBtn('⏮', 'First page', this.currentPage <= 1);
        firstBtn.addEventListener('click', function() {
            if (self.currentPage > 1) { self.currentPage = 1; self._emitPageChange(); }
        });
        paginationEl.appendChild(firstBtn);

        // Prev
        var prevBtn = this._createPaginationBtn('◀', 'Previous page', this.currentPage <= 1);
        prevBtn.addEventListener('click', function() {
            if (self.currentPage > 1) { self.currentPage -= 1; self._emitPageChange(); }
        });
        paginationEl.appendChild(prevBtn);

        // Page label
        var pageLabel = document.createElement('span');
        pageLabel.className = 'pagination-label';
        pageLabel.textContent = 'Page ' + this.currentPage + ' of ' + totalPages;
        paginationEl.appendChild(pageLabel);

        // Next
        var nextBtn = this._createPaginationBtn('▶', 'Next page', this.currentPage >= totalPages);
        nextBtn.addEventListener('click', function() {
            if (self.currentPage < totalPages) { self.currentPage += 1; self._emitPageChange(); }
        });
        paginationEl.appendChild(nextBtn);

        // Last
        var lastBtn = this._createPaginationBtn('⏭', 'Last page', this.currentPage >= totalPages);
        lastBtn.addEventListener('click', function() {
            var tp = Math.max(1, Math.ceil(self.totalRows / self.rowsPerPage));
            if (self.currentPage < tp) { self.currentPage = tp; self._emitPageChange(); }
        });
        paginationEl.appendChild(lastBtn);

        // Rows per page selector
        var selectContainer = document.createElement('span');
        selectContainer.className = 'pagination-select-container';

        var select = document.createElement('select');
        select.className = 'pagination-select';
        for (var i = 0; i < this.rowsPerPageOptions.length; i++) {
            var opt = document.createElement('option');
            opt.value = this.rowsPerPageOptions[i];
            opt.textContent = this.rowsPerPageOptions[i];
            if (this.rowsPerPageOptions[i] === this.rowsPerPage) opt.selected = true;
            select.appendChild(opt);
        }
        select.addEventListener('change', function() {
            self.rowsPerPage = parseInt(this.value, 10);
            self.currentPage = 1;
            self._emitPageChange();
        });
        selectContainer.appendChild(select);
        paginationEl.appendChild(selectContainer);

        // Append after container
        this.container.appendChild(paginationEl);
    }

    _createPaginationBtn(text, title, disabled) {
        var btn = document.createElement('button');
        btn.className = 'pagination-btn';
        if (disabled) btn.className += ' pagination-btn-disabled';
        btn.textContent = text;
        btn.title = title;
        btn.disabled = disabled;
        return btn;
    }

    _emitPageChange() {
        this.renderPagination();
        if (typeof emitEvent === 'function') {
            emitEvent('pageChange', {
                page: this.currentPage,
                rowsPerPage: this.rowsPerPage
            });
        }
    }

    renderCell(cell) {
        var value = cell.getValue();
        var meta = (cell.column.columnDef.meta) || {};

        // A measured column's null is "not measured" -- shown, so it cannot be
        // read as "no" (a blank boolean cell) or as an absent value.
        if ((value === null || value === undefined) && meta.markNull) {
            return '<span class="not-measured" title="Not measured">&mdash;</span>';
        }
        if (value === null || value === undefined || value === '') return '';
        if (typeof value === 'boolean') {
            return value ? '<span class="check-icon">&#10003;</span>' : '';
        }
        if (meta.cellType === 'badge' && meta.badgeConfig) {
            var key = String(value);
            var cfg = meta.badgeConfig[key] || meta.badgeConfig[key.toLowerCase()];
            if (cfg) {
                if (cfg.show === false) return '';
                return '<span class="badge" style="background-color: ' + cfg.color + '" title="' + value + '">' + cfg.label + '</span>';
            }
            return String(value);
        }
        if (typeof value === 'number') {
            var text = Number.isInteger(value) ? String(value) : value.toFixed(2);
            // A threshold scale: the first rule whose bounds the value satisfies
            // wins, and a rule with no bounds is the fallback. The colour is
            // computed from the raw value, not from `text`, which is rounded to
            // two places and would put 0.0455 on the wrong side of 0.05.
            if (meta.cellType === 'scale' && meta.badgeConfig) {
                var rules = meta.badgeConfig.scale || [];
                for (var ri = 0; ri < rules.length; ri++) {
                    var rule = rules[ri];
                    if (rule.below !== undefined && !(value < rule.below)) continue;
                    if (rule.above !== undefined && !(value > rule.above)) continue;
                    return '<span class="scale-value" style="color: ' + rule.color + '">' + text + '</span>';
                }
            }
            return text;
        }
        return String(value);
    }

    renderInfoButton() {
        // No longer a menu of outbound links: the gene dialog holds those, plus
        // the coordinates and the genome browsers. Hence an info glyph rather
        // than the external-link arrow this used to be.
        if (!this.roles.primaryKey) return '';
        var infoIcon = '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#38ada9" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>';
        return '<button class="gene-info-btn" title="Coordinates, genome browsers and external links for this gene">' + infoIcon + '</button>';
    }
}

window.GeneTrekTable = GeneTrekTable;
