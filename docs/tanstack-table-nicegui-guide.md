# Integrating TanStack Table (Vanilla JS) with NiceGUI

A practical guide based on the GeneTrek project. Covers architecture, the JS↔Python bridge, CDN loading, state management, and every gotcha encountered along the way.

---

## Table of Contents

1. [Architecture overview](#1-architecture-overview)
2. [Loading TanStack from CDN](#2-loading-tanstack-from-cdn)
3. [The critical `initialState` bug](#3-the-critical-initialstate-bug)
4. [JS ↔ Python bridge](#4-js--python-bridge)
5. [Server-side data, client-side pagination](#5-server-side-data-client-side-pagination)
6. [Sorting](#6-sorting)
7. [Dynamic column visibility](#7-dynamic-column-visibility)
8. [Custom cell rendering](#8-custom-cell-rendering)
9. [Horizontal scrolling with sticky columns](#9-horizontal-scrolling-with-sticky-columns)
10. [NiceGUI-specific pitfalls](#10-nicegui-specific-pitfalls)
11. [Minimal working example](#11-minimal-working-example)
12. [Checklist](#12-checklist)

---

## 1. Architecture overview

```
┌─────────────────────────────────────────────────────┐
│  Python (NiceGUI)                                   │
│                                                     │
│  AppConfig  ──▶  GeneTable (bridge component)       │
│  state dict       │                                 │
│                   ├─ _inject_scripts()   injects JS  │
│                   ├─ _push_page_data()   Python→JS  │
│                   └─ _handle_*()         JS→Python   │
└───────────────────┬─────────────────────────────────┘
                    │  ui.run_javascript()  (push)
                    │  emitEvent()          (receive)
                    ▼
┌─────────────────────────────────────────────────────┐
│  Browser (Vanilla JS)                               │
│                                                     │
│  TanStack Table core (from CDN)                     │
│  GeneTrekTable class                                │
│    ├─ createTable()   with state callbacks           │
│    ├─ render()        DOM construction               │
│    ├─ renderPagination()  client-side controls       │
│    └─ emitEvent()     page-change / sort-change      │
└─────────────────────────────────────────────────────┘
```

Key decisions:

- **TanStack Table core** is framework-agnostic — you get `createTable()` and row models, but zero DOM rendering. You build the `<table>` yourself.
- **Python owns the data.** JS only holds the current page of rows.
- **JS owns the UI state** (current page, rows per page, sort indicators). Python is notified via events.
- **JS files are read at startup** and injected via `ui.add_head_html()`. There is no static file serving — a server restart is required after editing JS.

---

## 2. Loading TanStack from CDN

### The problem

NiceGUI does not serve static JS files by default. You can use `app.add_static_files()`, but the simplest approach is CDN + dynamic `import()`.

### Why NOT `<script type="module">`

Using `<script type="module">` creates an **isolated scope**. Any class or function you define inside is invisible to the rest of the page. Your inline `onclick` handlers, your `window._geneTableInstance`, your NiceGUI `ui.run_javascript()` calls — none of them can see module-scoped variables.

### The correct pattern

Use a regular `<script>` with a dynamic `import()`:

```python
TANSTACK_CDN = "https://cdn.jsdelivr.net/npm/@tanstack/table-core/+esm"

ui.add_head_html(f"""
<script>
(function() {{
    import('{TANSTACK_CDN}').then(function(mod) {{
        // Expose what you need globally
        window.TanStackTable = {{
            createTable: mod.createTable,
            getCoreRowModel: mod.getCoreRowModel,
        }};

        // Your table class definition goes here (inline or concatenated)
        {my_table_js_source}
    }}).catch(function(err) {{
        console.error('Failed to load TanStack:', err);
    }});
}})();
</script>
""")
```

The outer IIFE keeps temporary variables contained while `window.TanStackTable` and `window.MyTableClass` remain globally accessible.

### Wait-for-ready pattern

The CDN import is async. Your Python init code must poll until both TanStack and your class are available:

```javascript
(function tryInit(attempt) {
    var container = document.getElementById('my-table-container');
    if (!window.TanStackTable || !window.MyTableClass || !container) {
        if (attempt < 50) {
            setTimeout(function() { tryInit(attempt + 1); }, 200);
        } else {
            console.error('Table init failed after 50 attempts');
        }
        return;
    }
    if (window._tableInstance) return; // already initialized
    window._tableInstance = new MyTableClass('my-table-container', options);
})(1);
```

And on the Python side, wrap the initialization in a deferred timer so the DOM exists:

```python
ui.timer(0.1, lambda: ui.run_javascript(init_js), once=True)
```

---

## 3. The critical `initialState` bug

> **This is the single biggest gotcha with vanilla JS TanStack Table.**

### The symptom

After calling `createTable()`, the very first call to `table.getHeaderGroups()` crashes:

```
Cannot read properties of undefined (reading 'left')
```

The error comes from `columnPinning` being `undefined` in the active state.

### The root cause

React, Vue, and Solid adapters for TanStack Table **automatically merge `initialState` into the active state**. The vanilla JS `createTable()` does **not**. Features like column pinning, column visibility, and grouping set defaults in `initialState`, but the runtime `state` object starts empty.

### The fix

Immediately after `createTable()`, manually merge `initialState` into the active state:

```javascript
var table = TanStack.createTable({ /* options */ });

// CRITICAL: Seed the active state with feature defaults
table.setOptions(function(prev) {
    return Object.assign({}, prev, {
        state: Object.assign({}, table.initialState, prev.state),
    });
});
```

This one line prevents crashes from any TanStack feature that relies on `initialState` defaults (column pinning, visibility, grouping, expansion, etc.).

---

## 4. JS ↔ Python bridge

### Python → JS: `ui.run_javascript()`

Push data or trigger actions from Python:

```python
data_json = json.dumps(page_data, default=str)
ui.run_javascript(f"""
    if (window._tableInstance) {{
        window._tableInstance.setPageData({data_json}, {total_rows});
    }}
""")
```

**Guard with `if (window._tableInstance)`** — race conditions are real. The JS class may not be instantiated yet.

For data pushes that must wait, use a self-retrying wrapper:

```python
ui.run_javascript(f"""
    (function pushData() {{
        if (!window._tableInstance) {{
            setTimeout(pushData, 100);
            return;
        }}
        window._tableInstance.setPageData({data_json}, {total_rows});
    }})();
""")
```

### JS → Python: `emitEvent()` + `ui.on()`

NiceGUI provides a global `emitEvent(name, data)` function that fires a custom event the Python side can listen to.

**JS side:**
```javascript
emitEvent('sortChange', {
    sorting: self.sorting.map(function(s) {
        return { id: s.id, desc: s.desc };
    })
});
```

**Python side:**
```python
ui.on("sort-change", self._handle_sort_change)

def _handle_sort_change(self, e):
    data = e.args
    sorting = data.get("sorting", [])
    # ... process sorting
```

### Event naming: the camelCase trap

> **NiceGUI's `ui.on()` converts event names to kebab-case internally.**

If your JS calls `emitEvent('sortChange', ...)`, you must listen with:

```python
ui.on("sort-change", handler)   # ✅ kebab-case
ui.on("sortChange", handler)    # ❌ will never fire
```

This is **not documented**. NiceGUI normalizes camelCase to kebab-case when registering event listeners. `emitEvent('pageChange', ...)` maps to `ui.on("page-change", ...)`.

---

## 5. Server-side data, client-side pagination

### Why hybrid?

- With 60k+ rows, sending all data to the client is impractical.
- With pure server pagination, the UI feels sluggish (every page turn is a round-trip).
- Hybrid: Python slices the data, JS renders the page and controls the pagination UI.

### Data flow

1. JS pagination controls call `emitEvent('pageChange', { page, rowsPerPage })`.
2. Python handler updates `state["current_page"]` and `state["rows_per_page"]`.
3. Python slices `filtered_df` with `df.slice(start_idx, rows_per_page)`.
4. Python pushes the page slice to JS via `ui.run_javascript()`.
5. JS receives data and calls `table.setOptions()` + `render()`.

### Updating TanStack with new data

When new data arrives (page change, filter change, sort change), update the table options:

```javascript
setPageData(data, totalRows) {
    var self = this;
    this.data = data;
    this.totalRows = totalRows;
    this.table.setOptions(function(prev) {
        return Object.assign({}, prev, {
            data: self.data,
            state: self.table.getState(),  // preserve current state
        });
    });
    this.render();
}
```

**Always pass `state: self.table.getState()`** when calling `setOptions()`. Otherwise TanStack resets the state to defaults and you lose sorting, pinning, etc.

---

## 6. Sorting

### Client-side sort state, server-side sort execution

TanStack's `onSortingChange` fires when the user clicks a column header. The vanilla JS version receives an updater function:

```javascript
onSortingChange: function(updater) {
    self.sorting = typeof updater === 'function'
        ? updater(self.sorting)
        : updater;

    // Update TanStack's internal state
    self.table.setOptions(function(prev) {
        return Object.assign({}, prev, {
            state: Object.assign({}, self.table.getState(), {
                sorting: self.sorting
            }),
        });
    });

    // Re-render to show sort indicators
    self.render();

    // Notify Python to re-sort the full dataset
    emitEvent('sortChange', {
        sorting: self.sorting.map(function(s) {
            return { id: s.id, desc: s.desc };
        })
    });
}
```

### Python sort handler

```python
def handle_sort(sorting):
    sort_cols = [s["id"] for s in sorting if s["id"] in df.columns]
    descending = [s.get("desc", False) for s in sorting]
    if sort_cols:
        state["filtered_df"] = state["filtered_df"].sort(
            sort_cols, descending=descending, nulls_last=True
        )
```

After sorting, reset to page 1 and push fresh data.

---

## 7. Dynamic column visibility

Store a master list of all column definitions and a current subset:

```javascript
setAllColumnDefs(allDefs) {
    this._allColumnDefs = allDefs;
}

setColumnVisibility(selectedColumns) {
    this.columnDefs = this._allColumnDefs.filter(function(def) {
        return selectedColumns.indexOf(def.id) !== -1;
    });
    this.table.setOptions(function(prev) {
        return Object.assign({}, prev, {
            columns: self.buildColumns(),
            state: self.table.getState(),
        });
    });
    this.render();
}
```

Call from Python when the user changes column selection:

```python
def update_column_visibility(self):
    selected = json.dumps(self.state["selected_columns"])
    ui.run_javascript(f"""
        if (window._tableInstance) {{
            window._tableInstance.setColumnVisibility({selected});
        }}
    """)
```

---

## 8. Custom cell rendering

Since vanilla JS TanStack gives you no built-in rendering, you own the entire cell pipeline. Read cell values and metadata to decide what to output:

```javascript
renderCell(cell) {
    var value = cell.getValue();
    var meta = cell.column.columnDef.meta || {};

    if (value === null || value === undefined || value === '') return '';

    if (typeof value === 'boolean') {
        return value ? '<span class="check-icon">✓</span>' : '';
    }

    if (meta.cellType === 'badge' && meta.badgeConfig) {
        var cfg = meta.badgeConfig[String(value)];
        if (cfg) {
            if (cfg.show === false) return '';
            return '<span class="badge" style="background:'
                + cfg.color + '">' + cfg.label + '</span>';
        }
    }

    if (typeof value === 'number') {
        return Number.isInteger(value) ? String(value) : value.toFixed(2);
    }

    return String(value);
}
```

Pass metadata through `columnDef.meta`:

```javascript
buildColumns() {
    return this.columnDefs.map(function(def) {
        return {
            accessorKey: def.id,
            header: def.header,
            meta: {
                cellType: def.cellType,
                badgeConfig: def.badgeConfig,
                tooltip: def.tooltip,
            },
        };
    });
}
```

---

## 9. Horizontal scrolling with sticky columns

When the table has more columns than the viewport can show, you need horizontal scrolling. This requires a specific DOM and CSS structure.

### DOM structure (built by JS)

```
<div class="table-scroll-container">     ← overflow-x: auto (scrollbar lives here)
  <div class="table-container">           ← overflow-x: clip; overflow-y: visible
    <table class="my-table">
      <thead>...</thead>
      <tbody>...</tbody>
    </table>
  </div>
</div>
```

### Key CSS

```css
.table-scroll-container {
    overflow-x: auto;
    max-width: 100%;
    width: 100%;
    box-sizing: border-box;
}

.table-container {
    overflow-x: clip;      /* prevents decorative overflow from adding scroll width */
    overflow-y: visible;   /* allows upward overflow like angled headers */
    min-width: fit-content;
}

.my-table {
    border-collapse: separate;  /* required for sticky columns */
    border-spacing: 0;
    width: max-content;         /* table grows with columns */
    min-width: 100%;            /* but fills container when columns are few */
}
```

### Sticky first column

```css
.my-table th:first-child,
.my-table td:first-child {
    position: sticky;
    left: 0;
    z-index: 2;
}

/* Explicit backgrounds — 'inherit' doesn't work when content scrolls behind */
.my-table tbody tr:nth-child(odd) td:first-child {
    background-color: #f7fafc;
}
.my-table tbody tr:nth-child(even) td:first-child {
    background-color: white;
}
.my-table tbody tr:hover td:first-child {
    background-color: #edf2f7;
}
```

> **`background-color: inherit` breaks on sticky columns.** When content scrolls behind a sticky cell, `inherit` resolves to the `<tr>`'s background — which is offscreen. You must set explicit colors.

### NiceGUI width containment

NiceGUI wraps your `ui.html()` elements in anonymous `<div>` wrappers. These wrappers have `overflow: visible` by default, so a wide table pushes them (and their parents) wider than the viewport.

Fix: add `overflow-x-hidden` (Tailwind class) to the NiceGUI parent column, **and** constrain the wrapper chain via CSS:

```python
container = ui.column().classes("items-center w-full overflow-x-hidden")
gene_table.container.move(container)
```

```css
/* Constrain NiceGUI wrapper divs to parent width */
.overflow-x-hidden > * {
    max-width: 100%;
    box-sizing: border-box;
}

#my-table-container {
    max-width: 100%;
    box-sizing: border-box;
}
```

### The `overflow-x: auto` + `overflow-y: visible` trap

**CSS does not allow `overflow-x: auto` with `overflow-y: visible`.** When one axis is `auto` or `scroll`, the browser forces the other to `auto`. This clips vertical overflow (like headers that extend above the table).

**Workaround:** Use `padding-top` on the scroll container instead of `margin-top` on the table. Content within padding is not clipped, so headers extending into the padding area remain visible.

```javascript
// In JS, after calculating header height:
scrollContainer.style.paddingTop = (headerHeight - 25) + 'px';
```

### The `overflow-x: clip` vs `hidden` distinction

`overflow: clip` prevents content from contributing to scroll width **without** creating a new block formatting context. Unlike `hidden`, `clip` can coexist with `overflow-y: visible` on the same element. Use it on the inner `.table-container` to prevent decorative overflow (CSS transforms, skewed headers, etc.) from inflating the outer scroll container's scroll width.

---

## 10. NiceGUI-specific pitfalls

### JS files are read once at startup

NiceGUI reads JS files when `ui.add_head_html()` is called (during page registration). Editing a `.js` file has no effect until you restart the server.

### `ui.html()` wrapper divs

`ui.html('<div id="foo"></div>')` creates a NiceGUI wrapper `<div>` *around* your div. The chain is:

```
nicegui-column > div(wrapper) > div#foo
```

The wrapper div has no `max-width` by default, so content inside can push it wider than its parent column. Always add `max-width: 100%` via CSS on `#foo` and its parent.

### Timer-based initialization

The DOM from `ui.html()` is not immediately available when the Python constructor runs. Use `ui.timer(0.1, ..., once=True)` to defer initialization:

```python
ui.timer(0.1, lambda: ui.run_javascript(init_js), once=True)
```

For initial data push, use a longer delay to account for CDN loading:

```python
ui.timer(0.5, lambda: table.update_data(), once=True)
```

### Quasar/NiceGUI z-index conflicts

NiceGUI uses Quasar components. Quasar dialogs use `z-index: 6000`. If you have fixed-position elements (drawer toggles, floating buttons), keep their z-index below 6000 (e.g. 5000) so dialogs appear on top.

### `ui.on()` is session-scoped

`ui.on("event-name", handler)` registers the handler for the current user session. It does not need cleanup, but be aware it only fires for events from the same browser tab.

---

## 11. Minimal working example

### Python side (`table_component.py`)

```python
import json
from pathlib import Path
from nicegui import ui

TANSTACK_CDN = "https://cdn.jsdelivr.net/npm/@tanstack/table-core/+esm"

class DataTable:
    def __init__(self, data, columns):
        self.data = data
        self.columns = columns
        self.container_id = "data-table"

        # Inject JS
        table_js = Path("table.js").read_text()
        ui.add_head_html(f"""
        <script>
        (function() {{
            import('{TANSTACK_CDN}').then(function(mod) {{
                window.TanStackTable = {{
                    createTable: mod.createTable,
                    getCoreRowModel: mod.getCoreRowModel,
                }};
                {table_js}
            }});
        }})();
        </script>
        """)

        # Create container
        self.container = ui.html(
            f'<div id="{self.container_id}"></div>',
            sanitize=False,
        )

        # Deferred init
        col_json = json.dumps(columns)
        data_json = json.dumps(data)
        ui.timer(0.1, lambda: ui.run_javascript(f"""
            (function tryInit(n) {{
                if (!window.TanStackTable || !window.SimpleTable) {{
                    if (n < 50) setTimeout(function() {{ tryInit(n+1) }}, 200);
                    return;
                }}
                window._table = new SimpleTable('{self.container_id}', {{
                    columns: {col_json},
                    data: {data_json}
                }});
            }})(1);
        """), once=True)

        # Listen for sort events
        ui.on("sort-change", self._on_sort)

    def _on_sort(self, e):
        sorting = e.args.get("sorting", [])
        # Re-sort self.data, push new data to JS
        # ...
```

### JS side (`table.js`)

```javascript
class SimpleTable {
    constructor(containerId, options) {
        this.container = document.getElementById(containerId);
        this.data = options.data || [];
        this.sorting = [];

        var TanStack = window.TanStackTable;
        var self = this;

        this.table = TanStack.createTable({
            data: this.data,
            columns: options.columns.map(function(c) {
                return { accessorKey: c.id, header: c.header };
            }),
            state: { sorting: this.sorting },
            onStateChange: function(updater) {
                self.table.setOptions(function(prev) {
                    return Object.assign({}, prev, {
                        state: updater(self.table.getState()),
                    });
                });
            },
            onSortingChange: function(updater) {
                self.sorting = typeof updater === 'function'
                    ? updater(self.sorting) : updater;
                self.table.setOptions(function(prev) {
                    return Object.assign({}, prev, {
                        state: Object.assign({}, self.table.getState(), {
                            sorting: self.sorting
                        }),
                    });
                });
                self.render();
                if (typeof emitEvent === 'function') {
                    emitEvent('sortChange', { sorting: self.sorting });
                }
            },
            getCoreRowModel: TanStack.getCoreRowModel(),
        });

        // CRITICAL: merge initialState into active state
        this.table.setOptions(function(prev) {
            return Object.assign({}, prev, {
                state: Object.assign({}, self.table.initialState, prev.state),
            });
        });

        this.render();
    }

    render() {
        if (!this.container) return;
        this.container.innerHTML = '';

        var table = document.createElement('table');
        var thead = document.createElement('thead');
        var headerGroups = this.table.getHeaderGroups();

        for (var gi = 0; gi < headerGroups.length; gi++) {
            var tr = document.createElement('tr');
            var headers = headerGroups[gi].headers;
            for (var hi = 0; hi < headers.length; hi++) {
                var th = document.createElement('th');
                th.textContent = headers[hi].column.columnDef.header;
                th.style.cursor = 'pointer';

                var handler = headers[hi].column.getToggleSortingHandler();
                if (handler) {
                    (function(h) {
                        th.addEventListener('click', function(e) { h(e); });
                    })(handler);
                }
                tr.appendChild(th);
            }
            thead.appendChild(tr);
        }
        table.appendChild(thead);

        var tbody = document.createElement('tbody');
        var rows = this.table.getRowModel().rows;
        for (var ri = 0; ri < rows.length; ri++) {
            var tr = document.createElement('tr');
            var cells = rows[ri].getVisibleCells();
            for (var ci = 0; ci < cells.length; ci++) {
                var td = document.createElement('td');
                td.textContent = cells[ci].getValue();
                tr.appendChild(td);
            }
            tbody.appendChild(tr);
        }
        table.appendChild(tbody);
        this.container.appendChild(table);
    }

    setData(data) {
        var self = this;
        this.data = data;
        this.table.setOptions(function(prev) {
            return Object.assign({}, prev, {
                data: self.data,
                state: self.table.getState(),
            });
        });
        this.render();
    }
}

window.SimpleTable = SimpleTable;
```

---

## 12. Checklist

Before you ship, verify each of these:

- [ ] **`initialState` merge** — `table.setOptions()` merges `initialState` into `state` right after `createTable()`
- [ ] **CDN loading** — Dynamic `import()` inside a regular `<script>`, not `<script type="module">`
- [ ] **Global scope** — Table class and TanStack are assigned to `window.*`
- [ ] **Wait-for-ready** — Retry loop in JS before instantiation; `ui.timer()` in Python
- [ ] **Event names** — JS `emitEvent('camelCase')` matches Python `ui.on("kebab-case")`
- [ ] **State preservation** — Every `setOptions()` call includes `state: self.table.getState()`
- [ ] **Guard clauses** — Every `ui.run_javascript()` wraps calls in `if (window._tableInstance)`
- [ ] **Sticky backgrounds** — Explicit `background-color` on sticky `td` elements (not `inherit`)
- [ ] **Width containment** — `overflow-x-hidden` on NiceGUI parent + `max-width: 100%` on wrapper divs
- [ ] **Overflow axis coupling** — `padding-top` on scroll container, not `margin-top`, when using `overflow-x: auto`
- [ ] **`border-collapse: separate`** — Required for CSS transforms and sticky columns on table cells
- [ ] **Server restart** — After any JS file edit (NiceGUI reads JS at startup, not at runtime)
