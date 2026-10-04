"""Project skills share link checks without opening private or generated trees."""

import json

import pytest

from scripts import check_docs_links as links


def write_doc(repo, name, text="# Example\n"):
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_source_scan_includes_skills_and_prunes_excluded_trees(tmp_path, monkeypatch):
    included = {
        "README.md",
        "docs/developer/documentation.md",
        ".agents/skills/demo/SKILL.md",
        ".agents/skills/demo/references/review.md",
    }
    excluded = {
        ".agents/README.md",
        ".agents/local/notes.md",
        ".agents/skills/demo/node_modules/dependency/README.md",
        ".agents/skills/demo/build/generated.md",
        ".codex/skills/local/SKILL.md",
        ".aws/README.md",
        "docs/private/notes.md",
        "docs/.vitepress/cache.md",
        "docs/node_modules/dependency/README.md",
        "android-tv/.gradle/README.md",
        "android-tv/app/build/generated.md",
        "output/report.md",
        "data/README.md",
        "media/README.md",
    }
    for name in included | excluded:
        write_doc(tmp_path, name)

    visited = set()
    original_walk = links.os.walk

    def recording_walk(*args, **kwargs):
        for directory, dirs, names in original_walk(*args, **kwargs):
            visited.add(links.Path(directory).relative_to(tmp_path).as_posix())
            yield directory, dirs, names

    monkeypatch.setattr(links.os, "walk", recording_walk)
    assert {path.relative_to(tmp_path).as_posix()
            for path in links.markdown_files(tmp_path, tmp_path)} == included
    assert not visited.intersection({"docs/private", ".agents/local", ".codex", "data", "media"})
    for root in ["docs/private", ".agents/local", ".codex", "output"]:
        assert links.markdown_files(tmp_path / root, tmp_path) == []


def test_symlinks_do_not_bypass_source_or_target_exclusions(tmp_path):
    repo = tmp_path / "repo"
    source = write_doc(repo, ".agents/skills/demo/SKILL.md")
    ordinary = write_doc(repo, "docs/example.md")
    private = write_doc(repo, "docs/private/notes.md")
    outside = write_doc(tmp_path, "outside/secret.md")
    skill_dir = source.parent
    (skill_dir / "outside.md").symlink_to(outside)
    (skill_dir / "outside-dir").symlink_to(outside.parent, target_is_directory=True)
    (skill_dir / "private.md").symlink_to(private)
    (skill_dir / "private-dir").symlink_to(private.parent, target_is_directory=True)
    local_dir = repo / ".agents/local"
    local_dir.mkdir()
    (local_dir / "alias.md").symlink_to(ordinary)
    (local_dir / "alias-dir").symlink_to(ordinary.parent, target_is_directory=True)

    assert links.markdown_files(repo, repo) == sorted([source, ordinary])
    for root in [skill_dir / "outside.md", skill_dir / "outside-dir",
                 skill_dir / "private.md", skill_dir / "private-dir",
                 local_dir / "alias.md", local_dir / "alias-dir"]:
        assert links.markdown_files(root, repo) == []


def test_skill_root_checks_links_and_references(tmp_path, monkeypatch, capsys):
    write_doc(tmp_path, "docs/developer/documentation.md", "# Documentation\n\n## Rules\n")
    skill = write_doc(tmp_path, ".agents/skills/demo/SKILL.md",
                      "# Demo\n\n[规范](../../docs/developer/documentation.md#rules)\n"
                      "[审核](references/review.md#review)\n")
    review = write_doc(tmp_path, ".agents/skills/demo/references/review.md",
                       "# Review\n\n[规则](../../../../docs/developer/documentation.md#missing)\n")
    monkeypatch.setattr(links, "__file__", str(tmp_path / "scripts/check_docs_links.py"))
    monkeypatch.setattr(links.sys, "argv", ["check_docs_links.py", "--root", ".agents/skills/demo"])

    assert links.main() == 1
    output = capsys.readouterr().out
    assert "检查 2 个文件，发现 2 个问题" in output
    assert "链接目标不存在" in output
    assert "锚点 #missing" in output

    skill.write_text(skill.read_text().replace("../../docs/", "../../../docs/"), encoding="utf-8")
    review.write_text(review.read_text().replace("#missing", "#rules"), encoding="utf-8")
    assert links.main() == 0
    assert "检查 2 个文件，发现 0 个问题" in capsys.readouterr().out


def test_explicit_root_keeps_lexical_exclusions(tmp_path, monkeypatch, capsys):
    docs = write_doc(tmp_path, "docs/example.md").parent
    local = tmp_path / ".agents/local"
    local.mkdir(parents=True)
    (local / "alias").symlink_to(docs, target_is_directory=True)
    monkeypatch.setattr(links, "__file__", str(tmp_path / "scripts/check_docs_links.py"))
    monkeypatch.setattr(links.sys, "argv", ["check_docs_links.py", "--root", ".agents/local/alias"])

    assert links.main() == 0
    assert "检查 0 个文件，发现 0 个问题" in capsys.readouterr().out


def test_explicit_external_symlink_root_is_rejected(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    skill = write_doc(repo, ".agents/skills/demo/SKILL.md")
    outside = write_doc(tmp_path, "outside/secret.md")
    (skill.parent / "outside").symlink_to(outside.parent, target_is_directory=True)
    monkeypatch.setattr(links, "__file__", str(repo / "scripts/check_docs_links.py"))
    monkeypatch.setattr(links.sys, "argv", ["check_docs_links.py", "--root", ".agents/skills/demo/outside"])

    with pytest.raises(SystemExit) as exc:
        links.main()
    assert exc.value.code == 2


def test_source_list_reuses_scope_without_reading_markdown(tmp_path, monkeypatch, capsys):
    write_doc(tmp_path, "README.md", "[Broken](missing.md)\n")
    write_doc(tmp_path, ".agents/skills/demo/SKILL.md")
    write_doc(tmp_path, "docs/private/notes.md")
    monkeypatch.setattr(links, "__file__", str(tmp_path / "scripts/check_docs_links.py"))
    monkeypatch.setattr(links.sys, "argv", ["check_docs_links.py", "--list"])
    assert links.main() == 0
    assert json.loads(capsys.readouterr().out) == [".agents/skills/demo/SKILL.md", "README.md"]


def test_explicit_html_ids_work_for_local_and_cross_page_links(tmp_path):
    target = write_doc(tmp_path, "docs/deployment.md", """# Deployment

<span id="check-service"></span>

## Step 1：检查服务

<span
  class="compatibility-anchor"
  id='docker-paths'
></span>

<a ID=CaseSensitive></a>

[检查](#check-service) · [路径](#docker-paths)
""")
    source = write_doc(tmp_path, "README.md", """# Project

[检查](docs/deployment.md#check-service)
[路径](docs/deployment.md#docker-paths)
[大小写](docs/deployment.md#CaseSensitive)
""")

    assert links.check_file(target, tmp_path) == []
    assert links.check_file(source, tmp_path) == []
    # Keep the existing source heading convention; explicit IDs bypass renderer differences.
    assert "step-1检查服务" in links.headings_of(target)
    source.write_text(source.read_text().replace("#CaseSensitive", "#casesensitive"))
    assert len(links.check_file(source, tmp_path)) == 1


@pytest.mark.parametrize(("opening", "decoys", "closing"), [
    ("````html", "```\n~~~", "`````"),
    ("~~~~html", "```\n~~~", "~~~~"),
    ("```html", "~~~", "```"),
])
def test_fenced_examples_do_not_supply_ids_or_headings(tmp_path, opening, decoys, closing):
    target = write_doc(tmp_path, "docs/example.md", f"""# Example

{opening}
<span id="example-id"></span>
## Example heading
{decoys}
<span id="still-in-example"></span>
## Still in example
{closing}

<span id="real-id"></span>
## Real heading
""")
    source = write_doc(tmp_path, "README.md", """# Project

[示例](docs/example.md#example-id)
[内层](docs/example.md#still-in-example)
[标题](docs/example.md#example-heading)
[内层标题](docs/example.md#still-in-example)
[正文](docs/example.md#real-id)
[正文标题](docs/example.md#real-heading)
""")

    assert links.headings_of(target) == {"example", "real-id", "real-heading"}
    assert len(links.check_file(source, tmp_path)) == 4


def test_comments_code_spans_and_escaped_html_do_not_supply_ids(tmp_path):
    target = write_doc(tmp_path, "example.md", """# `Example`

<!-- <span id="commented"></span> -->
<!--
<span id="multiline-comment"></span>
-->
`<span id="inline-example"></span>`
`` `<span id="nested-code-example"></span>` ``
&lt;span id="escaped-example"&gt;&lt;/span&gt;
<span id="real-id"></span>
""")

    assert links.headings_of(target) == {"example", "real-id"}
