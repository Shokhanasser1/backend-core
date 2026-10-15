"""Template catalog rendering (interfaces §3.4)."""

from pathlib import Path

from core.notifications.registry import TemplateDef, TemplateRegistry


def _registry_with(tmp_path: Path, subject: str) -> TemplateRegistry:
    for locale in ("ru", "uz"):
        (tmp_path / locale).mkdir()
        (tmp_path / locale / "demo.hello.txt").write_text("Hi $name\n", encoding="utf-8")
        (tmp_path / locale / "demo.hello.subject.txt").write_text(subject, encoding="utf-8")
    registry = TemplateRegistry()
    registry.register("demo", [TemplateDef("demo.hello", ("email",))], tmp_path)
    return registry


def test_subject_is_a_single_line_without_file_trailing_newline(tmp_path: Path) -> None:
    # Template files end with a newline (end-of-file-fixer); an email Subject
    # header must not carry it into the inbox.
    rendered = _registry_with(tmp_path, "Hello $name\n").render("demo.hello", "ru", {"name": "A"})
    assert rendered.subject == "Hello A"
