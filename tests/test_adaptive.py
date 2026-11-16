"""自适应元素定位：指纹、相似度、存储与页面改版自愈。"""

from __future__ import annotations

from focus_crawler.config.schema import TaskConfig
from focus_crawler.core import adaptive
from focus_crawler.core.parser import parse_detail
from focus_crawler.core.storage import Storage

PAGE_V1 = """
<html><body>
  <div class="post">
    <div class="title-box"><h1 class="headline">今天的学习笔记</h1></div>
    <div class="meta"><span class="author">张三</span></div>
    <div class="content"><p>正文内容第一段。</p></div>
  </div>
</body></html>
"""

# 页面改版：class 全变、层级多包了一层，但文本与语义没变
PAGE_V2 = """
<html><body>
  <section class="article-wrap">
    <div class="article">
      <header class="article-head"><h1 class="article-title">今天的学习笔记</h1></header>
      <div class="byline"><span class="writer">张三</span></div>
      <article class="body"><p>正文内容第二版。</p></article>
    </div>
  </section>
</body></html>
"""


def _task(fields: dict) -> TaskConfig:
    return TaskConfig.from_dict(
        {
            "task_name": "t",
            "start_urls": ["https://blog.test/post/1"],
            "allowed_domains": ["blog.test"],
            "detail": {"fields": fields},
        }
    )


def test_fingerprint_and_score():
    root = adaptive.parse_html(PAGE_V1)
    element = root.xpath("//h1")[0]
    fp = adaptive.fingerprint(element)
    assert fp.tag == "h1"
    assert "学习笔记" in fp.text
    assert fp.parent_tag == "div"

    same = adaptive.score(fp, fp)
    assert same == 100.0

    other = adaptive.fingerprint(root.xpath("//p")[0])
    assert adaptive.score(fp, other) < 60


def test_relocate_finds_element_after_markup_change():
    old_root = adaptive.parse_html(PAGE_V1)
    fp = adaptive.fingerprint(old_root.xpath("//h1")[0])
    new_root = adaptive.parse_html(PAGE_V2)
    found, matched = adaptive.relocate(new_root, fp, threshold=35)
    assert found is not None
    assert found.text_content().strip() == "今天的学习笔记"
    assert matched >= 35


def test_adaptive_parse_recovers_after_redesign(tmp_path):
    store = Storage(tmp_path / "t.db")
    task_id = store.upsert_task("t", "{}")
    task = _task(
        {
            "title": {"selector": "div.title-box > h1.headline", "adaptive": True},
            "author": {"selector": ".meta .author"},
        }
    )

    first = parse_detail(PAGE_V1, "https://blog.test/post/1", task.detail, adaptive_store=_store(store, task_id))
    assert first.data["title"] == "今天的学习笔记"
    assert first.hits["title"] is True
    # 指纹已保存
    assert store.get_adaptive_fingerprint(task_id, "title", "blog.test") is not None

    # 改版：原选择器失效，靠指纹重新定位
    second = parse_detail(PAGE_V2, "https://blog.test/post/1", task.detail, adaptive_store=_store(store, task_id))
    assert second.data["title"] == "今天的学习笔记"
    assert second.hits["title"] is True


def test_without_adaptive_flag_field_stays_empty_after_redesign(tmp_path):
    store = Storage(tmp_path / "t.db")
    task_id = store.upsert_task("t", "{}")
    task = _task({"title": {"selector": "div.title-box > h1.headline"}})
    parse_detail(PAGE_V1, "https://blog.test/post/1", task.detail, adaptive_store=_store(store, task_id))
    second = parse_detail(PAGE_V2, "https://blog.test/post/1", task.detail, adaptive_store=_store(store, task_id))
    assert second.hits["title"] is False


def test_adaptive_stats_and_clear(tmp_path):
    store = Storage(tmp_path / "t.db")
    task_id = store.upsert_task("t", "{}")
    store.save_adaptive_fingerprint(task_id, "title", "blog.test", "{}")
    store.save_adaptive_fingerprint(task_id, "title", "blog.test", '{"tag":"h1"}')
    rows = store.list_adaptive_fingerprints(task_id)
    assert len(rows) == 1
    assert rows[0]["relocations"] == 1
    assert store.get_adaptive_fingerprint(task_id, "title", "blog.test") == '{"tag":"h1"}'
    store.clear_adaptive_fingerprints(task_id)
    assert store.list_adaptive_fingerprints(task_id) == []


def _store(storage: Storage, task_id: int) -> adaptive.AdaptiveStore:
    return adaptive.AdaptiveStore(storage, task_id)
