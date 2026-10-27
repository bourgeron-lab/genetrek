"""CSS styles for the GeneTrek UI."""

from __future__ import annotations

MAIN_CSS = """
/* Prevent horizontal scrollbar from pushing drawer toggles */
body, html {
    overflow-x: hidden;
    margin: 0;
    padding: 0;
}

.q-page-container {
    overflow-x: hidden;
    margin-right: 0 !important;
    padding-right: 0 !important;
}

.q-layout {
    margin-right: 0 !important;
}

/* Constrain the gene-table-container and its NiceGUI wrapper so the
   table can't push them wider than the parent column.
   The wrapper chain is: .nicegui-column > div(c9) > #gene-table-container > .table-scroll-container */
.overflow-x-hidden > * {
    max-width: 100%;
    box-sizing: border-box;
}

#gene-table-container {
    max-width: 100%;
    box-sizing: border-box;
}

/* Scroll wrapper — provides horizontal scrollbar when table exceeds viewport.
   padding-top accommodates diagonal headers (set dynamically by JS). */
.table-scroll-container {
    overflow-x: auto;
    max-width: 100%;
    width: 100%;
    position: relative;
    padding-top: 90px;
    box-sizing: border-box;
}

/* Inner wrapper — the table lives here.
   overflow-x:clip prevents skewed header overhang from adding scroll width,
   while overflow-y:visible allows diagonal headers to extend upward.
   padding-right accommodates the last trapezoid's skew overhang so its text
   isn't clipped (set dynamically by JS alongside header height). */
.table-container {
    overflow-x: clip;
    overflow-y: visible;
    min-width: fit-content;
    position: relative;
}

/* Search drawer toggle - fixed position always visible */
.drawer-toggle-fixed {
    position: fixed !important;
    left: 0 !important;
    top: 50% !important;
    transform: translateY(-50%) !important;
    height: auto !important;
    width: 28px !important;
    min-width: 28px !important;
    cursor: pointer;
    display: flex !important;
    align-items: center;
    justify-content: center;
    background-color: #1a365d !important;
    transition: background-color 0.2s;
    z-index: 5000 !important;
    padding: 12px 4px !important;
    border-radius: 0 4px 4px 0 !important;
    visibility: visible !important;
    opacity: 1 !important;
    box-shadow: 2px 0 5px rgba(0,0,0,0.2);
}

.drawer-toggle-fixed:hover {
    background-color: #2c5282 !important;
}

/* Right drawer toggle - fixed position always visible */
.drawer-toggle-fixed-right {
    position: fixed !important;
    right: 0 !important;
    top: 50% !important;
    transform: translateY(-50%) !important;
    height: auto !important;
    width: 28px !important;
    min-width: 28px !important;
    cursor: pointer;
    display: flex !important;
    align-items: center;
    justify-content: center;
    background-color: #1a365d !important;
    transition: background-color 0.2s;
    z-index: 5000 !important;
    padding: 12px 4px !important;
    border-radius: 4px 0 0 4px !important;
    visibility: visible !important;
    opacity: 1 !important;
    box-shadow: -2px 0 5px rgba(0,0,0,0.2);
}

.drawer-toggle-fixed-right:hover {
    background-color: #2c5282 !important;
}

.toggle-text-container {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 2px;
}

.drawer-toggle-text {
    writing-mode: vertical-rl;
    text-orientation: mixed;
    font-weight: bold;
    font-size: 11px;
    letter-spacing: 1px;
    transform: rotate(180deg);
    color: white !important;
    margin: 0;
    padding: 0;
    line-height: 1.2;
}

/* Search token badges */
.token-editor-container {
    background: white;
    border: 1px solid #ccc;
    border-radius: 4px;
    padding: 8px;
    min-height: 225px;
    width: 100%;
    box-sizing: border-box;
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    align-items: flex-start;
    align-content: flex-start;
    cursor: text;
}
.search-token-badge {
    color: white;
    padding: 4px 8px;
    border-radius: 999px;
    font-size: 12px;
    display: inline-flex;
    align-items: center;
    gap: 4px;
    white-space: nowrap;
}
.search-token-text {
    max-width: 150px;
    overflow: hidden;
    text-overflow: ellipsis;
}
.search-token-remove {
    cursor: pointer;
    font-weight: bold;
    font-size: 14px;
    line-height: 1;
    opacity: 0.8;
    margin-left: 2px;
}
.search-token-remove:hover {
    opacity: 1;
}
.token-input {
    flex: 1 1 100px;
    min-width: 100px;
    border: none;
    outline: none;
    font-size: 14px;
    padding: 4px;
    background: transparent;
    color: #333;
}

.gene-table {
    border-collapse: separate;
    border-spacing: 0;
    font-size: 12px;
    width: max-content;
    min-width: 100%;
}

.gene-table thead {
    position: sticky;
    top: 0;
    z-index: 1;
}

.gene-table th {
    color: white;
    font-weight: 600;
    font-size: 13px;
    padding: 0;
    text-align: center;
    user-select: none;
    height: 30px;
    min-width: 36px;
    width: 36px;
    position: relative;
    overflow: visible;
    vertical-align: bottom;
    border-bottom: 2px solid #2c5282;
    background: none;
}

/* Diagonal label container — skewed parallelogram with horizontal top/bottom */
.gene-table th .th-wrapper {
    position: absolute;
    bottom: 0;
    left: 0;
    width: 100%;
    height: 120px;
    transform-origin: bottom center;
    transform: skewX(-45deg);
    background-color: #1a365d;
    border: 1px solid #2c5282;
    border-bottom: none;
    border-radius: 4px 4px 0 0;
    overflow: visible;
    box-sizing: border-box;
}

/* Text label — counter-skewed + rotated to read along the diagonal */
.th-text {
    position: absolute;
    bottom: 18px;
    /* The two skews below cancel, so the label lands in the page as a plain
       45 degree strip whose *thickness* is its line box -- and a 45 degree
       strip eats thickness * sqrt(2) of the column's 36px width. At 13px that
       is ~18px, and this offset was tuned by measuring the gap either side of
       the strip until they matched: ~7px each. Retune it if the font changes.
       Do not add vertical padding to fatten the click target. Padding thickens
       the strip towards the upper left until it overruns the parallelogram,
       and no `bottom`/`left` nudge cancels that: an offset in the element's
       own box reaches the page through the rotation, mixing x into y. (The 50%
       resolves against the wrapper's padding box, 34px, not the column.) */
    left: calc(50% + 9px);
    transform-origin: bottom left;
    transform: skewX(45deg) rotate(-45deg);
    white-space: nowrap;
    line-height: 1;
    font-size: 13px;
    /* The label is its own click target: it opens the column dialog rather
       than sorting. The glyphs are the target, which the bigger font already
       made easier to hit than the 11px it replaced. */
    cursor: help;
    text-decoration: underline dotted rgba(255, 255, 255, 0.45);
    text-underline-offset: 2px;
}

.th-text:hover {
    color: #38ada9;
    text-decoration-color: #38ada9;
}

/* Sort indicator — sits at the bottom of the parallelogram */
.th-sort-indicator {
    position: absolute;
    bottom: 2px;
    left: 50%;
    transform: skewX(45deg) translateX(-50%);
    font-size: 11px;
    line-height: 1;
}

/* `line-height` is a length, not the inherited unitless 1.5, and that is what
   keeps every row the same height. A unitless value is recomputed against each
   element's own font-size, so the 16px check mark below was getting a 24px line
   box in a row whose floor is 27px -- six pixels taller than a row whose
   boolean columns are all empty.

   The 18px here must stay equal to the `height` on `.check-icon` and `.badge`:
   those are boxes sized to fill exactly one line, and a mismatch reintroduces
   the same bug. */
.gene-table td {
    border-bottom: 1px solid #e2e8f0;
    border-right: 1px solid #e2e8f0;
    padding: 4px 8px;
    line-height: 18px;
    text-align: center;
    white-space: nowrap;
}
.gene-table tbody tr:first-child td {
    border-top: 1px solid #e2e8f0;
}

/* Zebra striping */
.gene-table tbody tr:nth-child(odd) {
    background-color: #f7fafc;
}
.gene-table tbody tr:nth-child(even) {
    background-color: white;
}
.gene-table tbody tr:hover {
    background-color: #edf2f7;
}

/* An atomic box of exactly one line, aligned to the top of it, rather than a
   glyph sitting on the baseline. Capping the cell's line-height is not enough
   on its own: a 16px Roboto glyph's content area is ~18.7px, so half-leading
   still pushes it out of an 18px line box. Sizing the box removes the font
   metrics from the question entirely, and `font-size` is untouched so the mark
   looks exactly as it did. Height must match `.gene-table td`'s line-height. */
.check-icon {
    color: #38ada9;
    font-size: 16px;
    font-weight: bold;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    height: 18px;
    line-height: 1;
    vertical-align: top;
}

/* "Not measured": a null in a gene list or in a column with a declared
   universe. Muted, so it reads as an absence of data rather than a value. */
.not-measured {
    color: #b0b7bf;
    display: inline-flex;
    align-items: center;
    height: 18px;
    line-height: 1;
    vertical-align: top;
}

/* Same treatment as `.check-icon`, for the same reason: as an inline-block its
   20.5px box also out-measured the row floor, so badge rows were taller too.
   Height must match `.gene-table td`'s line-height. */
.badge {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    height: 18px;
    line-height: 1;
    vertical-align: top;
    padding: 0 8px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 600;
    color: white;
    text-align: center;
}

/* Symbol column (first column) - right-aligned */
.gene-table th:first-child,
.gene-table td:first-child {
    min-width: 90px;
    text-align: right;
    position: sticky;
    left: 0;
    z-index: 2;
}
.gene-table th:first-child {
    z-index: 3;
    background-color: white;
    color: #1a365d;
    border-right: none;
    border-bottom: 2px solid #2c5282;
}
.gene-table th:first-child .th-wrapper {
    position: static;
    transform: none;
    background: none;
    border: none;
    border-radius: 0;
    height: auto;
    width: auto;
    display: flex;
    flex-direction: row;
    justify-content: flex-end;
    align-items: center;
    gap: 2px;
    padding: 6px 8px;
    overflow: visible;
}
.gene-table th:first-child .th-text {
    position: static;
    transform: none;
    white-space: nowrap;
    padding: 0;
    text-decoration-color: rgba(26, 54, 93, 0.4);
}
.gene-table th:first-child .th-sort-indicator {
    position: static;
    transform: none;
    padding: 0;
    color: #1a365d;
}
.gene-table td:first-child {
    font-weight: 600;
    border-left: 1px solid #e2e8f0;
}
/* Sticky first-column needs explicit bg so it covers scrolled content */
.gene-table tbody tr:nth-child(odd) td:first-child {
    background-color: #f7fafc;
}
.gene-table tbody tr:nth-child(even) td:first-child {
    background-color: white;
}
.gene-table tbody tr:hover td:first-child {
    background-color: #edf2f7;
}

/* Action column header (last th, no diagonal — fully transparent) */
.gene-table th:last-child {
    background: none;
    border-bottom: 2px solid transparent;
    width: 40px;
    min-width: 40px;
}

/* Sort indicators */
th.sortable {
    cursor: pointer;
}
/* Hovering the trapezoid promises a sort, so it must not light up when the
   pointer is on the label -- clicking there opens the column dialog instead.
   The label has its own teal hover, so each region says what it will do. */
th.sortable:not(:first-child):hover:not(:has(.th-text:hover)) .th-wrapper {
    background-color: #2c5282;
}
th.sortable:first-child:hover {
    background-color: #edf2f7;
}
th.sortable:not(.sort-asc):not(.sort-desc) .th-sort-indicator {
    opacity: 0.4;
}

/* JS-rendered pagination controls */
.genetrek-pagination {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 10px;
    margin-top: 16px;
    padding: 8px 0;
}

.pagination-btn {
    background: none;
    border: 1px solid #38ada9;
    color: #38ada9;
    cursor: pointer;
    border-radius: 4px;
    padding: 4px 10px;
    font-size: 14px;
    transition: background-color 0.2s, color 0.2s;
}

.pagination-btn:hover:not(:disabled) {
    background-color: #38ada9;
    color: white;
}

.pagination-btn-disabled,
.pagination-btn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
    border-color: #a0aec0;
    color: #a0aec0;
}

.pagination-label {
    font-size: 14px;
    font-weight: 600;
    color: #2c5282;
    padding: 0 8px;
}

.pagination-select-container {
    margin-left: 16px;
    display: flex;
    align-items: center;
    gap: 4px;
}

.pagination-select {
    border: 1px solid #cbd5e0;
    border-radius: 4px;
    padding: 4px 8px;
    font-size: 13px;
    color: #333;
    background: white;
    cursor: pointer;
}

/* Action column: one button per row, opening that gene's dialog */
.action-column {
    text-align: center;
    min-width: 40px !important;
    max-width: 40px !important;
    padding: 0 !important;
}

.gene-info-btn {
    background: none;
    border: none;
    cursor: pointer;
    font-size: 16px;
    padding: 4px;
    display: flex;
    align-items: center;
    justify-content: center;
}

.gene-info-btn:hover {
    opacity: 0.7;
}

/* The gene symbol opens the same dialog, so it needs the same kind of hint the
   column labels carry -- in the first column's dark ink rather than white. */
.gene-symbol {
    cursor: pointer;
    text-decoration: underline dotted rgba(26, 54, 93, 0.4);
    text-underline-offset: 2px;
}

.gene-symbol:hover {
    color: #38ada9;
    text-decoration-color: #38ada9;
}

/* Checkbox color */
.q-checkbox__inner--truthy .q-checkbox__bg {
    background-color: #38ada9 !important;
    border-color: #38ada9 !important;
}

/* ---------------------------------------------------------------------------
   Column selector tree.

   Quasar sizes tree rows for touch: 4px of padding plus a 3px top margin on
   every header, and a 40px checkbox hit area, which comes to ~32px a row. The
   selector has 75 nodes, so that is ~2,400px of scrolling to read a list whose
   whole point is being scannable. These rules pull a row down to ~20px. The
   checkbox keeps a 24px target -- still clickable, and the label is a target
   too, so precision is not the constraint.
   --------------------------------------------------------------------------- */
.column-tree .q-tree__node {
    padding-bottom: 0;
}

.column-tree .q-tree__node-header {
    padding: 0 4px;
    margin-top: 0;
    min-height: 0;
}

.column-tree .q-tree__node-header-content {
    line-height: 18px;
}

/* Quasar's connector lines are pseudo-elements sized against the header's own
   padding box, so squeezing that padding leaves them as detached stubs rather
   than a tree. Dropped instead of patched: at three columns the indent and the
   bold group headings carry the nesting on their own, and the lines were
   spending horizontal room the labels want. */
.column-tree.q-tree--standard .q-tree__node::after,
.column-tree.q-tree--standard .q-tree__node::before,
.column-tree.q-tree--standard .q-tree__node-header::after,
.column-tree.q-tree--standard .q-tree__node-header::before {
    display: none;
}

/* The 40px default is most of a row's height on its own, and it -- not the
   line height -- is what sets the floor: at 24px the rows would not go below
   24px however tight the text was. 20px still leaves a comfortable target,
   and the label is a target too. */
.column-tree .q-checkbox__inner {
    width: 20px;
    min-width: 20px;
    height: 20px;
    font-size: 20px;
}

/* Expand arrows are sized for touch too, and there is one on every parent. */
.column-tree .q-tree__arrow,
.column-tree .q-tree__spinner {
    font-size: 18px;
    margin-right: 2px;
    padding: 0;
}

/* 25px of indent per level, three levels deep, spends horizontal room the
   labels want -- and the connector lines already carry the nesting. */
.column-tree .q-tree__children {
    padding-left: 16px;
}

/* Categories and subcategories read as headings rather than as more leaves;
   without it 75 near-identical rows are hard to scan at this density. */
.column-tree .q-tree__node--parent > .q-tree__node-header
    .q-tree__node-header-content {
    font-weight: 600;
}

/* The dialog is ~1150px wide and one column of labels is ~330px, so two thirds
   of it was empty while the list scrolled for four screens. Flowing the
   top-level categories into columns spends that width instead: ~1,800px of
   list becomes ~650px. `column-width` rather than `column-count` so a narrow
   window simply gets fewer columns.

   330px, not narrower: 250px does fit a fourth column and saves another
   ~130px, but it wraps the longest labels ("PanelApp AUS Progressive myoclonic
   epilepsy", "Constrained (union of shet and LOEUF)") onto two lines, and
   choppy rows cost more scanning than the extra column saves.

   The tree is a flex item in Quasar's scroll-area content, so it sizes to its
   own content unless told otherwise -- without the width there is nothing for
   the columns to divide. */
.column-tree {
    width: 100%;
    column-width: 330px;
    column-gap: 28px;
}

/* Three column-break guards, which only work as a set. Measured in Firefox,
   because that is the only engine that shows the difference: Chromium renders
   every combination below identically, and `break-after: avoid` -- the obvious
   way to keep a heading with what follows it -- is silently ignored by Firefox
   inside a multi-column container, so `break-inside` is the only lever there
   is.

     guards                        orphaned headings   split rows   stray gaps
     category                              0                1            0
     category + row                        0                0            1
     row + subgroup                        2                0            0
     category + subgroup + row             0                0            0  <-

   Each guard answers one of those columns. The category guard stops a heading
   being stranded at the foot of a column with everything it labels starting in
   the next one. A category too tall to fit whole -- "NDD gene lists", 23 rows in
   five subgroups -- still has to break somewhere, and the subgroup and row
   guards are what push that break onto a subgroup boundary instead of through
   the middle of a row, where it strands a checkbox from its label.

   Note the `q-tree__node-collapsible` in the subgroup selector: Quasar wraps a
   node's children in it, so a path straight from `.q-tree__node` to
   `.q-tree__children` matches nothing -- silently, as CSS always does. */
.column-tree > .q-tree__node,
.column-tree
    > .q-tree__node
    > .q-tree__node-collapsible
    > .q-tree__children
    > .q-tree__node--parent,
.column-tree .q-tree__node-header,
.column-tree .q-tree__node--child {
    break-inside: avoid;
}

/* A numeric cell coloured by a threshold scale from badges.yaml. Only the
   weight is set here: the colour is per-value and arrives inline. Deliberately
   not a filled chip like `.badge` -- these columns carry a number on every one
   of 45,000 rows, and a wall of chips would drown the badge columns beside
   them. */
.gene-table .scale-value {
    font-weight: 600;
}

/* Tables inside rendered markdown. Tailwind's preflight collapses tables and
   zeroes every border, and nothing else in the bundle styles a bare <table> --
   `.gene-table` above sets its own borders for exactly this reason. Without
   these rules the prose pages' tables come out as unaligned text, which is
   most of what a changelog is. */
.nicegui-markdown table {
    border-collapse: collapse;
    margin: 12px 0;
    font-size: 12px;
}

.nicegui-markdown th,
.nicegui-markdown td {
    border: 1px solid #e2e8f0;
    padding: 5px 10px;
    text-align: left;
    vertical-align: top;
}

.nicegui-markdown th {
    background-color: #edf2f7;
    color: #1a365d;
    font-weight: 600;
    white-space: nowrap;
}

/* Numbers read better right-aligned, and every numeric column in these tables
   is the last one or close to it -- but aligning by position would be wrong,
   so this only offers the class for a cell that asks. */
.nicegui-markdown td.numeric {
    text-align: right;
    font-variant-numeric: tabular-nums;
}
"""
