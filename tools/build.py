#!/usr/bin/env python3
"""把 data/日期.json 检查一遍，生成文字简报和对话稿。

用法：python3 tools/build.py 2026-10-09
检查不通过时列出所有问题并以非零状态退出，不生成任何文件。
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (编号前缀, 板块名, 最少条数, 最多条数)
SECTIONS = [
    ("T", "贸易", 2, 4),
    ("W", "战争与冲突", 2, 4),
    ("N", "新兴产业", 2, 4),
    ("P", "公众新兴趣与潮流", 2, 4),
    ("L", "各国新法新政", 2, 4),
    ("AI", "AI 新发展", 2, 4),
    ("X", "X 科技圈动态", 2, 5),
]

TITLE_MAX = 30          # 标题字数上限
BODY_MAX_SENTENCES = 3  # 解释最多几句
WHY_MAX = 60            # 「为什么重要」字数上限
BRIEF_MAX = 3600        # 全文可见字数上限，约 10 分钟读完
QUESTION_MAX = 80
TURN_MAX = 160          # 对话稿单句台词字数上限
PODCAST_MIN, PODCAST_MAX = 1100, 2100  # 对话稿总字数，约 4–8 分钟
CHARS_PER_MINUTE = 265  # 估算时长用

LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
CITATION = re.compile(r"\s*[(（]\s*\[[^\]]+\]\(https?://[^)\s]+\)\s*[)）]")


def visible(text):
    """去掉引用链接后读者实际读到的文字。"""
    text = CITATION.sub("", text)
    return LINK.sub(r"\1", text)


def check_item(label, item, errors):
    for key in ("title", "body", "why"):
        value = item.get(key)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{label}：缺少 {key}")
            return []
        if "\n" in value.strip():
            errors.append(f"{label}：{key} 里不能换行")

    title, body, why = item["title"].strip(), item["body"].strip(), item["why"].strip()
    if len(title) > TITLE_MAX:
        errors.append(f"{label}：标题 {len(title)} 字，超过 {TITLE_MAX} 字")
    if LINK.search(title) or LINK.search(why):
        errors.append(f"{label}：标题和「为什么重要」里不放链接")
    if why.startswith("为什么重要"):
        errors.append(f"{label}：why 不要自带「为什么重要：」，生成时会自动加")
    if len(why) > WHY_MAX:
        errors.append(f"{label}：「为什么重要」{len(why)} 字，超过 {WHY_MAX} 字")

    sentences = len(re.findall(r"[。！？!?]", visible(body)))
    if sentences > BODY_MAX_SENTENCES:
        errors.append(f"{label}：解释有 {sentences} 句，最多 {BODY_MAX_SENTENCES} 句")

    sources = item.get("sources")
    if not isinstance(sources, list) or not 1 <= len(sources) <= 3:
        errors.append(f"{label}：sources 需要 1–3 个来源")
        return []
    urls = []
    for source in sources:
        name, url = (source or {}).get("name"), (source or {}).get("url")
        if not name or not isinstance(url, str) or not url.startswith("https://"):
            errors.append(f"{label}：每个来源都要有 name 和 https:// 开头的 url")
            continue
        urls.append(url)

    inline = [url for _, url in LINK.findall(body)]
    if not inline:
        errors.append(f"{label}：解释里至少要有一处来源链接，格式是 ([来源名](URL))")
    for url in inline:
        if url not in urls:
            errors.append(f"{label}：解释里的链接不在 sources 里：{url}")
    return urls


def check(data, date, previous_urls):
    errors = []
    if data.get("date") != date:
        errors.append(f"date 应该是 {date}")
    coverage = data.get("coverage")
    if not isinstance(coverage, str) or not coverage.strip():
        errors.append("缺少 coverage（覆盖的时间范围，例如「10 月 6 日至 10 月 9 日」）")

    sections = data.get("sections")
    if not isinstance(sections, dict):
        return errors + ["缺少 sections"]
    unknown = set(sections) - {code for code, *_ in SECTIONS}
    if unknown:
        errors.append(f"sections 里有不认识的板块：{'、'.join(sorted(unknown))}")

    seen, total = {}, 0
    for code, name, low, high in SECTIONS:
        items = sections.get(code)
        if not isinstance(items, list):
            errors.append(f"缺少板块 {code}（{name}）")
            continue
        if not low <= len(items) <= high:
            errors.append(f"板块 {code}（{name}）有 {len(items)} 条，应为 {low}–{high} 条")
        for index, item in enumerate(items, 1):
            label = f"{code}{index}"
            if not isinstance(item, dict):
                errors.append(f"{label}：格式不对")
                continue
            for url in check_item(label, item, errors):
                if url in seen:
                    errors.append(f"{label}：和 {seen[url]} 用了同一个来源链接，可能是重复的新闻")
                elif url in previous_urls:
                    errors.append(f"{label}：这个来源上一期已经用过，换成有新进展的报道或去掉：{url}")
                seen.setdefault(url, label)
            if all(isinstance(item.get(k), str) for k in ("title", "body", "why")):
                total += len(item["title"]) + len(visible(item["body"])) + len(item["why"])
    if total > BRIEF_MAX:
        errors.append(f"全文 {total} 字，超过 {BRIEF_MAX} 字（10 分钟读不完），请删减")

    question = data.get("question")
    if not isinstance(question, str) or not question.strip():
        errors.append("缺少 question")
    else:
        question = question.strip()
        if len(question) > QUESTION_MAX:
            errors.append(f"question {len(question)} 字，超过 {QUESTION_MAX} 字")
        if not question.endswith(("？", "?")):
            errors.append("question 要写成一个问句")
        if "值得想一想" in question:
            errors.append("question 只写问题本身，不要带「今天值得想一想」")

    turns = data.get("podcast")
    if not isinstance(turns, list) or len(turns) < 10:
        return errors + ["podcast 至少要有 10 句台词"]
    spoken = 0
    for index, turn in enumerate(turns, 1):
        speaker, text = (turn or {}).get("speaker"), (turn or {}).get("text")
        if speaker not in ("A", "B") or not isinstance(text, str) or not text.strip():
            errors.append(f"对话稿第 {index} 句：需要 speaker（A 或 B）和 text")
            continue
        expected = "A" if index % 2 else "B"
        if speaker != expected:
            errors.append(f"对话稿第 {index} 句：A、B 要轮流说，这一句应该是 {expected}")
        text = text.strip()
        if len(text) > TURN_MAX:
            errors.append(f"对话稿第 {index} 句：{len(text)} 字，超过 {TURN_MAX} 字，拆成两轮")
        if re.search(r"https?://|[\[\]#*_`<>]|\n", text):
            errors.append(f"对话稿第 {index} 句：只能有要念出来的文字，不能有链接、Markdown 符号或换行")
        spoken += len(text)
    if not PODCAST_MIN <= spoken <= PODCAST_MAX:
        errors.append(f"对话稿共 {spoken} 字，应在 {PODCAST_MIN}–{PODCAST_MAX} 字之间（约 4–8 分钟）")
    tail = "".join((turn or {}).get("text") or "" for turn in turns[-3:])
    if "值得想一想" not in tail:
        errors.append("对话稿最后三句里要说出「今天值得想一想」并提出那个问题")
    return errors


def render_brief(data, date):
    lines = [f"![[{date} 每日简报.mp3]]", "", f"# {date} 每日简报", "", f"*覆盖范围：{data['coverage'].strip()}*", ""]
    for code, name, _, _ in SECTIONS:
        lines += [f"## {code} {name}", ""]
        for index, item in enumerate(data["sections"][code], 1):
            sources = " · ".join(f"[{s['name']}]({s['url']})" for s in item["sources"])
            lines += [
                f"### {code}{index} {item['title'].strip()}",
                item["body"].strip(),
                f"为什么重要：{item['why'].strip()}",
                f"原文：{sources}",
                "",
            ]
    lines += ["## 🤔 今天值得想一想", data["question"].strip(), ""]
    lines += ["## ✍️ 批注区", "例如：T1、AI4 重要，其余删掉", ""]
    return "\n".join(lines)


def render_podcast(data, date):
    lines = [f"# {date} 对话稿", ""]
    lines += [f"{turn['speaker']}: {turn['text'].strip()}" for turn in data["podcast"]]
    return "\n".join(lines) + "\n"


def urls_of(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {
        source.get("url")
        for items in (data.get("sections") or {}).values()
        for item in items or []
        for source in (item or {}).get("sources") or []
    }


def main():
    if len(sys.argv) != 2 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", sys.argv[1]):
        sys.exit("用法：python3 tools/build.py YYYY-MM-DD")
    date = sys.argv[1]
    source = ROOT / "data" / f"{date}.json"
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except OSError:
        sys.exit(f"找不到 {source.relative_to(ROOT)}")
    except ValueError as error:
        sys.exit(f"{source.name} 不是合法的 JSON：{error}")

    earlier = sorted(p for p in (ROOT / "data").glob("*.json") if p.stem < date)
    errors = check(data, date, urls_of(earlier[-1]) if earlier else set())
    if errors:
        print(f"{source.name} 有 {len(errors)} 个问题，改完再运行：")
        for error in errors:
            print(f"- {error}")
        sys.exit(1)

    brief = ROOT / "briefs" / f"{date} 每日简报.md"
    podcast = ROOT / "podcast" / f"{date} 对话稿.md"
    brief.write_text(render_brief(data, date), encoding="utf-8")
    podcast.write_text(render_podcast(data, date), encoding="utf-8")

    count = sum(len(data["sections"][code]) for code, *_ in SECTIONS)
    spoken = sum(len(turn["text"].strip()) for turn in data["podcast"])
    print(f"已生成 {brief.relative_to(ROOT)}（{count} 条新闻）")
    print(f"已生成 {podcast.relative_to(ROOT)}（{spoken} 字，约 {spoken / CHARS_PER_MINUTE:.1f} 分钟）")


if __name__ == "__main__":
    main()
