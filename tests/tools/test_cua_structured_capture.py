import json
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def test_capture_prefers_structured_elements_and_dimensions():
    from tools.computer_use.cua_backend import CuaDriverBackend

    backend = CuaDriverBackend()
    backend._session = MagicMock()
    backend._session.call_tool.side_effect = [
        {"data": "", "images": [], "isError": False, "structuredContent": {
            "windows": [{"app_name": "Calculator", "pid": 7, "window_id": 9,
                         "is_on_screen": True, "title": "Calculator", "z_index": 0}]
        }},
        {"data": "legacy summary only", "images": [], "isError": False,
         "structuredContent": {
             "dimensions": {"width": 800, "height": 600},
             "elements": [
                 {"index": 1, "role": "AXButton", "label": "One",
                  "bounds": {"x": 10, "y": 20, "width": 30, "height": 40}},
                 {"index": 2, "role": "AXButton", "title": "Two",
                  "bounds": [50, 60, 70, 80]},
             ],
         }},
    ]

    result = backend.capture(mode="ax")

    assert (result.width, result.height) == (800, 600)
    assert [(e.index, e.role, e.label, e.bounds) for e in result.elements] == [
        (1, "AXButton", "One", (10, 20, 30, 40)),
        (2, "AXButton", "Two", (50, 60, 70, 80)),
    ]


def test_cli_raw_window_state_preserves_structured_capture_fields():
    from scripts.telegram_desktop_cua_smoke import _CuaDriverCliSession
    from tools.computer_use.cua_backend import CuaDriverBackend

    windows_payload = {
        "windows": [{
            "app_name": "Calculator", "pid": 7, "window_id": 9,
            "is_on_screen": True, "title": "Calculator", "z_index": 0,
        }],
    }
    state_payload = {
        "element_count": 1,
        "elements": [{
            "element_index": 12,
            "role": "AXButton",
            "label": "One",
            "frame": {"x": 10, "y": 20, "w": 30, "h": 40},
        }],
        "screenshot_width": 1567,
        "screenshot_height": 1107,
        "screenshot_png_b64": "aW1hZ2U=",
        "tree_markdown": 'AXWindow "Calculator"',
    }
    completed = [
        subprocess.CompletedProcess([], 0, stdout=json.dumps(windows_payload), stderr=""),
        subprocess.CompletedProcess([], 0, stdout=json.dumps(state_payload), stderr=""),
    ]
    backend = CuaDriverBackend()
    backend._session = _CuaDriverCliSession("/tmp/cua-driver")

    with patch("scripts.telegram_desktop_cua_smoke.subprocess.run", side_effect=completed):
        result = backend.capture(mode="ax")

    assert (result.width, result.height) == (1567, 1107)
    assert [(e.index, e.role, e.label, e.bounds) for e in result.elements] == [
        (12, "AXButton", "One", (10, 20, 30, 40)),
    ]


def test_mcp_json_text_preserves_root_structured_fields():
    from tools.computer_use.cua_backend import _extract_tool_result

    payload = {
        "elements": [{"element_index": 3, "role": "AXButton"}],
        "screenshot_width": 1567,
        "screenshot_height": 1107,
    }
    mcp_result = SimpleNamespace(
        content=[SimpleNamespace(type="text", text=json.dumps(payload))],
        structuredContent=None,
        isError=False,
    )

    result = _extract_tool_result(mcp_result)

    assert result["data"] == payload
    assert result["structuredContent"] == payload
    assert result["images"] == []
    assert result["isError"] is False
