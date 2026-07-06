import json
import subprocess
import sys
from pathlib import Path


def test_inventory_symbols_lists_function_names_lines_and_sizes(tmp_path):
    sample = tmp_path / "sample.py"
    sample.write_text(
        "def small():\n"
        "    return 1\n"
        "\n"
        "class Example:\n"
        "    def method(self):\n"
        "        value = small()\n"
        "        return value\n"
    )
    output = tmp_path / "inventory.json"

    result = subprocess.run(
        [sys.executable, "scripts/inventory_symbols.py", "--json", "--output", str(output), str(sample)],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    data = json.loads(output.read_text())
    symbols = data["files"][0]["symbols"]
    assert symbols[0]["qualified_name"] == "small"
    assert symbols[0]["start_line"] == 1
    assert symbols[0]["end_line"] == 2
    assert symbols[0]["line_count"] == 2
    assert symbols[1]["qualified_name"] == "Example.method"
    assert symbols[1]["calls"] == ["small"]
