"""Column selector dialog component."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nicegui import ui

from genetrek.schema import ColumnGroup, in_display_order
from genetrek.ui.components.column_info_dialog import column_tooltip

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from genetrek.config.loader import AppConfig

#: Node id prefixes for the two parent levels. A column name can never collide
#: with either, because ``schema.FIELD_NAME_RE`` forbids ``:`` -- and a tree
#: keys its nodes by id, so a collision would let one node shadow another.
_CATEGORY_PREFIX = "cat:"
_SUBCATEGORY_PREFIX = "sub:"

#: The tree group holding the visitor's own lists. Not a declared category:
#: it exists per tab, and it always comes last, after every database column.
MY_LISTS_GROUP = "My lists"


def _with_user_lists(
    groups: Iterable[ColumnGroup],
    list_columns: Iterable[str],
) -> list[ColumnGroup]:
    """The database's groups, then one for the visitor's lists if any exist."""
    out = list(groups)
    columns = tuple(list_columns)
    if columns:
        out.append(ColumnGroup(MY_LISTS_GROUP, "", columns, ()))
    return out


def _with_locked(
    columns: Iterable[str],
    locked: str | None,
    order: Mapping[str, int],
) -> list[str]:
    """Put ``columns`` in display order, with ``locked`` always among them.

    This is what lets the selector leave a column out of its tree without the
    table losing it: whatever the tree reports, the table gets this. Nothing
    here pins a *position* -- ``locked`` is the symbol column, whose rank in
    ``order`` is 0, so it arrives first on its own. That matters because
    ``ui/styles.py`` styles the first column positionally.
    """
    selected = list(columns)
    if locked is not None and locked not in selected:
        selected.insert(0, locked)
    # A user list has no rank, so `in_display_order` puts it after every
    # database column, in the order it was ticked.
    return in_display_order(selected, order)


def _tree_nodes(
    groups: Iterable[ColumnGroup],
    labels: dict[str, str],
    locked: str | None = None,
    tooltips: Mapping[str, str] | None = None,
) -> list[dict]:
    """Build the tree's node list from grouped columns.

    Parent labels carry their column count: ticking a category ticks every
    column under it, and a large one makes the table's diagonal header
    noticeably taller, so the size is worth seeing beforehand.

    ``locked`` is left out entirely, counts included: it is always shown, so a
    checkbox for it would either lie or do nothing. A subgroup or group that
    holds nothing else is dropped rather than rendered empty.

    ``tooltips`` carries each column's hover text, the same string the table
    header shows. Parent nodes get none: a category is not a column and has no
    metadata to describe.
    """
    hover = tooltips or {}

    def leaves(columns: Iterable[str]) -> list[dict]:
        return [
            {"id": col, "label": labels.get(col, col), "tooltip": hover.get(col, "")}
            for col in columns
            if col != locked
        ]

    nodes: list[dict] = []
    for group in groups:
        children: list[dict] = leaves(group.columns)
        for sub in group.subgroups:
            sub_leaves = leaves(sub.columns)
            if not sub_leaves:
                continue
            children.append(
                {
                    "id": f"{_SUBCATEGORY_PREFIX}{group.name}/{sub.name}",
                    "label": f"{sub.name} ({len(sub_leaves)})",
                    "children": sub_leaves,
                },
            )
        if not children:
            continue
        offered = [col for col in group.all_columns if col != locked]
        nodes.append(
            {
                "id": f"{_CATEGORY_PREFIX}{group.name}",
                "label": f"{group.name} ({len(offered)})",
                "children": children,
            },
        )
    return nodes


def create_column_selector(
    config: AppConfig,
    state: dict[str, Any],
    on_change: Callable[[], None],
) -> ui.dialog:
    """Create the column visibility selector dialog."""
    column_dialog = ui.dialog()

    # The gene symbol, found through its role rather than by name. It is the
    # table's sticky first column and its row label, so it is offered nowhere
    # and removed by nothing. A database with no symbol role locks nothing,
    # which is exactly the behaviour this had before.
    locked = config.roles.symbol
    database_offered = [col for col in config.visible_columns if col != locked]
    # Mutated in place when the visitor's lists change, so every closure
    # below sees the current set.
    offered = list(database_offered)
    database_tooltips = {
        col: column_tooltip(config.display_config.get(col, {}))
        for col in config.visible_columns
    }

    def current_nodes() -> list[dict]:
        labels: dict[str, str] = state.get("list_labels", {})
        return _tree_nodes(
            _with_user_lists(config.column_groups, labels),
            {**config.column_names, **labels},
            locked,
            {
                **database_tooltips,
                **dict.fromkeys(labels, "Your list, stored in this browser only"),
            },
        )

    def apply_selection(columns: Iterable[str]) -> None:
        """Store a selection in display order and refresh the table once.

        Slice-assigned rather than rebound: `state` is a mutable bag shared
        with the table and the drawers, and this list's identity has always
        been stable.
        """
        state["selected_columns"][:] = _with_locked(
            columns, locked, config.column_order
        )
        state["current_page"] = 1
        on_change()

    def handle_tick(event: Any) -> None:  # noqa: ANN401
        """Apply the tree's whole ticked set.

        The payload is a state, not a delta, so one click on a category
        arrives as a single event and costs a single table refresh -- which is
        why no "bulk" guard is needed here. It is intersected with the offered
        columns rather than trusted: it comes from the browser, so an unknown
        name means a stale client and does nothing, and the intersection also
        drops any parent id the tick strategy happens to include.
        """
        ticked = set(event.value or ())
        apply_selection([c for c in offered if c in ticked])

    with column_dialog, ui.card().style("width: 1200px; max-width: 92vw;"):
        ui.label("Select Columns").classes("text-lg font-bold")
        if locked is not None:
            ui.label(
                f"{config.column_names.get(locked, locked)} is always shown.",
            ).classes("text-xs text-grey-7 -mt-1")

        with ui.row().classes("gap-2 mb-2 items-center w-full"):
            ui.button("All", on_click=lambda: set_all(value=True)).props("dense")
            ui.button("None", on_click=lambda: set_all(value=False)).props("dense")
            search = (
                ui.input(placeholder="Filter by column label")
                .props("outlined dense clearable")
                .classes("flex-grow")
            )

        # Viewport-relative, not a fixed 576px: the tree is ~650px tall once
        # the CSS tightens the rows and flows the categories into columns, so
        # a normal window shows the whole taxonomy at once rather than
        # scrolling for it. Capped so it cannot outgrow its own content.
        with ui.scroll_area().style("height: min(74vh, 800px)"):
            tree = ui.tree(
                current_nodes(),
                # "leaf-filtered", not "strict": under Quasar's `strict` every
                # node is ticked independently, so a category checkbox would
                # tick itself and leave its columns alone. The leaf strategies
                # are the ones that propagate a parent's tick to its children
                # and give parents a tri-state. "-filtered" restricts that
                # propagation to what the filter leaves visible, so a category
                # never ticks columns the user cannot see; with an empty filter
                # it behaves exactly like "leaf".
                tick_strategy="leaf-filtered",
                on_tick=handle_tick,
            ).classes("column-tree")
            # Quasar renders this slot inside `q-tree__node-header-content`,
            # leaving its own tickbox and arrow alone -- so the row keeps
            # working and only the label gains a native `title`. Vue omits the
            # attribute entirely when the value is empty, which is what keeps
            # categories and subcategories from showing a blank tooltip.
            tree.add_slot(
                "default-header",
                '<span :title="props.node.tooltip || undefined">'
                "{{ props.node.label }}</span>",
            )

        with ui.row().classes("w-full justify-between items-center mt-2"):
            # A new tab: navigating this one would tear down the table and
            # lose its filters, sort and pagination.
            ui.link("Browse all columns", "/columns", new_tab=True).classes(
                "text-sm",
            )
            ui.button("Close", on_click=column_dialog.close).props("flat")

    def set_all(*, value: bool) -> None:
        """Tick or untick every offered column.

        `tick`/`untick` change the prop without firing `on_tick`, so the table
        is refreshed once here rather than per column. The filter is cleared
        first because these buttons act on everything, and leaving a filter up
        while they ignore it reads as a bug.

        "None" empties the tree but not the table: `apply_selection` puts the
        locked column back, so the table keeps its row labels.
        """
        search.set_value(None)
        if value:
            # Never bare `tick()`: with no argument it ticks *every* node id,
            # parents included, and those then sit in the prop for good.
            tree.tick(offered)
        else:
            tree.untick(offered)
        apply_selection(offered if value else ())

    def handle_filter(event: Any) -> None:  # noqa: ANN401
        """Filter the tree, expanding so matches inside a category are visible.

        A filtered tree does not expand itself, and a child is filtered on its
        own label, so without this a match inside a collapsed category stays
        hidden. Quasar matches the label only, hence the input's placeholder.
        """
        tree.set_filter(event.value or "")
        tree.expand()

    search.on_value_change(handle_filter)

    # No `ticked=` constructor argument exists, so the starting selection is
    # applied here. `tick` mutates the prop without firing `on_tick`, so this
    # seeds the tree without triggering a table refresh. The locked column is
    # filtered out: it has no node, and an id for a node that does not exist
    # would sit in the prop for good.
    tree.tick([c for c in state["selected_columns"] if c != locked])
    tree.expand()

    def on_lists_change() -> None:
        """Offer the current lists, and forget ticks on deleted ones.

        ``state["selected_columns"]`` has already dropped deleted lists; the
        tree's own ticked prop has not, and a stale id there would come back
        on the next tick event.
        """
        offered[:] = database_offered + list(state.get("list_labels", {}))
        tree.props["nodes"] = current_nodes()
        tree.props["ticked"] = [
            c for c in state["selected_columns"] if c != locked and c in offered
        ]
        tree.update()
        # The tree was expanded before the lists arrived, so their group would
        # start collapsed -- the only one that does.
        tree.expand()

    state.setdefault("on_lists_change", []).append(on_lists_change)

    return column_dialog
