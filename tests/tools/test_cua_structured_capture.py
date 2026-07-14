from unittest.mock import MagicMock


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
