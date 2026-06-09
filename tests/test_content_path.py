# tests/test_content_path.py
import textwrap

from bully.runtime.runner import run_pipeline


def _project(tmp_path, rule_script):
    (tmp_path / ".bully.yml").write_text(textwrap.dedent(f"""\
        schema_version: 1
        rules:
          no-forbidden:
            description: "No FORBIDDEN marker."
            engine: script
            scope: ["*.py"]
            severity: error
            script: "{rule_script}"
    """))
    return tmp_path


def test_default_content_path_reads_file_on_disk(tmp_path):
    proj = _project(tmp_path, "grep -n FORBIDDEN {file} && exit 1 || exit 0")
    f = proj / "app.py"
    f.write_text("x = 1  # FORBIDDEN\n")  # the real file is dirty
    result = run_pipeline(str(proj / ".bully.yml"), str(f), diff="")
    assert result["status"] == "blocked"


def test_content_path_overrides_what_engines_read(tmp_path):
    proj = _project(tmp_path, "grep -n FORBIDDEN {file} && exit 1 || exit 0")
    clean = proj / "app.py"
    clean.write_text("x = 1\n")  # real file is CLEAN (scope matches this path)
    pending = proj / ".bully" / "tmp" / "pending.py"
    pending.parent.mkdir(parents=True)
    pending.write_text("x = 1  # FORBIDDEN\n")  # pending content is DIRTY
    result = run_pipeline(str(proj / ".bully.yml"), str(clean), diff="", content_path=str(pending))
    assert result["status"] == "blocked"  # engines read pending, not the clean real file


def test_content_path_clean_passes(tmp_path):
    proj = _project(tmp_path, "grep -n FORBIDDEN {file} && exit 1 || exit 0")
    real = proj / "app.py"
    real.write_text("x = 1  # FORBIDDEN\n")  # real dirty, but...
    pending = proj / ".bully" / "tmp" / "pending.py"
    pending.parent.mkdir(parents=True)
    pending.write_text("x = 1\n")  # ...pending is clean
    result = run_pipeline(str(proj / ".bully.yml"), str(real), diff="", content_path=str(pending))
    assert result["status"] == "pass"
