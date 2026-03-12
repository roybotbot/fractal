from __future__ import annotations

from pathlib import Path

from superpowers_runner_v2.artifacts import ArtifactWriter, extract_fenced_code


def test_extract_fenced_code_returns_first_code_block() -> None:
    output = """
    Here is the implementation.

    ```python
    def celsius_to_fahrenheit(celsius: float) -> float:
        return celsius * 9 / 5 + 32
    ```
    """.strip()

    code = extract_fenced_code(output)

    assert "def celsius_to_fahrenheit" in code
    assert "return celsius * 9 / 5 + 32" in code


def test_artifact_writer_writes_implementation_and_test_files(tmp_path: Path) -> None:
    writer = ArtifactWriter(base_dir=tmp_path)

    impl_path = writer.write_implementation(
        session_id="sess-123",
        task_name="celsius_to_fahrenheit",
        content="def celsius_to_fahrenheit(celsius: float) -> float:\n    return celsius * 9 / 5 + 32\n",
    )
    test_path = writer.write_test(
        session_id="sess-123",
        task_name="celsius_to_fahrenheit",
        content="def test_freezing_point() -> None:\n    assert celsius_to_fahrenheit(0) == 32\n",
    )

    assert impl_path == tmp_path / "sess-123" / "artifacts" / "celsius_to_fahrenheit.py"
    assert test_path == tmp_path / "sess-123" / "artifacts" / "test_celsius_to_fahrenheit.py"
    assert "def celsius_to_fahrenheit" in impl_path.read_text()
    assert "def test_freezing_point" in test_path.read_text()
