"""Retained cache choices survive later provider catalogue removals."""

import copy

from app.online import cache


SOURCE = "src_" + "1" * 32
ITEM = "itm_" + "2" * 32
LINE = "ln_" + "3" * 32
OTHER_LINE = "ln_" + "4" * 32


def identity(number, line=LINE):
    return {"source_id": SOURCE, "item_id": ITEM, "kind": "episode",
            "line_id": line, "episode_id": "ep_" + f"{number:032x}"}


def entry(number, line=LINE, label=None):
    return {"label": label or f"第{number}集", "identity": identity(number, line),
            "supported": True, "reason": "", "transport": "mp4"}


def snapshot(entries, *, title="旧标题", lines=None):
    return {"source": {"id": SOURCE, "name": "合成来源"},
            "item": {"id": ITEM, "kind": "tv", "title": title},
            "lines": lines or [{"id": LINE, "label": "旧线路", "entries": entries}]}


def row(number, detail, line=LINE):
    return {"id": f"oc_{number:032x}", "identity": identity(number, line),
            "state": "ready", "label": f"第{number}集", "detail": detail,
            "external_id": "synthetic-show"}


def read(monkeypatch, rows):
    monkeypatch.setattr(cache.ledger, "rows", lambda: copy.deepcopy(rows))
    monkeypatch.setattr(cache, "artwork_path", lambda _: None)
    return cache.item_detail(ITEM)


def test_later_snapshot_cannot_remove_ready_episode_and_does_not_rename_current_data(monkeypatch):
    old = snapshot([entry(1), entry(2, label="特别篇（上）"), entry(3)])
    latest = snapshot([entry(1, label="新版第一集"), entry(3)], title="最新标题")
    result = read(monkeypatch, [row(2, old), row(3, latest)])
    entries = result["detail"]["lines"][0]["entries"]
    assert [value["identity"] for value in entries] == [identity(1), identity(2), identity(3)]
    assert [value["label"] for value in entries] == ["新版第一集", "特别篇（上）", "第3集"]
    assert next(value for value in entries if value["identity"] == identity(2))["supported"]
    assert result["detail"]["item"]["title"] == "最新标题"
    assert [value["identity"] for value in latest["lines"][0]["entries"]] == [identity(1), identity(3)]


def test_multiple_removed_entries_follow_snapshot_anchors_independent_of_task_creation_order(monkeypatch):
    old = snapshot([entry(number) for number in range(1, 6)])
    latest = snapshot([entry(1), entry(5), entry(6)])
    result = read(monkeypatch, [row(3, old), row(2, old), row(4, old), row(6, latest)])
    assert [value["identity"] for value in result["detail"]["lines"][0]["entries"]] == [
        identity(number) for number in range(1, 7)]


def test_removed_line_restores_only_held_entries_at_exact_line_anchor(monkeypatch):
    old = snapshot([], lines=[{"id": OTHER_LINE, "label": "保留的旧版本", "entries": [entry(1, OTHER_LINE), entry(2, OTHER_LINE)]},
                              {"id": LINE, "label": "当前线路", "entries": [entry(1)]}])
    latest = snapshot([entry(1)])
    result = read(monkeypatch, [row(2, old, OTHER_LINE), row(1, latest)])
    lines = result["detail"]["lines"]
    assert [line["id"] for line in lines] == [OTHER_LINE, LINE]
    assert lines[0]["label"] == "保留的旧版本"
    assert [value["identity"] for value in lines[0]["entries"]] == [identity(2, OTHER_LINE)]
    assert [value["identity"] for value in lines[1]["entries"]] == [identity(1)]


def test_same_labels_do_not_substitute_other_episode_or_line(monkeypatch):
    old = snapshot([entry(1, label="正片"), entry(2, label="正片")])
    latest = snapshot([entry(3, label="正片")])
    result = read(monkeypatch, [row(2, old), row(3, latest)])
    assert {value["identity"]["episode_id"] for value in result["detail"]["lines"][0]["entries"]} == {
        identity(2)["episode_id"], identity(3)["episode_id"]}
