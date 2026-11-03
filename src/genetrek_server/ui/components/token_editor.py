"""Token editor component for search input."""

from __future__ import annotations

import json
from pathlib import Path

from nicegui import ui


def create_token_editor(
    token_colors_config: dict[str, str],
) -> ui.textarea:
    """Create the token editor widget. Returns the hidden textarea for value sync."""
    search_input = (
        ui.textarea()
        .classes("w-full")
        .props("outlined bg-color=white")
        .style("display: none;")
    )
    search_input_id = f"c{search_input.id}"

    ui.html(
        '<div id="token-editor-box" class="token-editor-container" '
        'style="width: 100%; min-width: 100%;">'
        '<input id="token-input-field" class="token-input" type="text" '
        'placeholder="Type gene identifiers..." autocomplete="off" />'
        "</div>",
        sanitize=False,
    ).classes("w-full")

    # Set config for JS, then inject the JS file
    js_path = Path(__file__).parent.parent / "js" / "token_editor.js"
    js_content = js_path.read_text()

    ui.add_body_html(
        f"<script>"
        f"window._tokenEditorConfig = {{"
        f"  hiddenTextareaId: '{search_input_id}',"
        f"  tokenColors: {json.dumps(token_colors_config)}"
        f"}};"
        f"{js_content}"
        f"</script>",
    )

    return search_input


def create_search_trigger(search_btn_id: str) -> None:
    """Inject JS to trigger tokenization on search button click and input blur."""
    ui.add_body_html(f"""
    <script>
    (function() {{
        function setupTriggers() {{
            const btn = document.getElementById('{search_btn_id}');
            const tokenInput = document.getElementById('token-input-field');

            if (!window.triggerTokenize) {{
                setTimeout(setupTriggers, 100);
                return;
            }}

            if (btn && !btn._tokenizeAttached) {{
                btn._tokenizeAttached = true;
                btn.addEventListener('click', function() {{
                    if (window.triggerTokenize) window.triggerTokenize();
                }}, true);
            }}
            if (tokenInput && !tokenInput._blurAttached) {{
                tokenInput._blurAttached = true;
                tokenInput.addEventListener('blur', function() {{
                    if (window.triggerTokenize) window.triggerTokenize();
                }});
            }}
        }}

        if (document.readyState === 'loading') {{
            document.addEventListener('DOMContentLoaded', setupTriggers);
        }} else {{
            setupTriggers();
        }}
    }})();
    </script>
    """)
