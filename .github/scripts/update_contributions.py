#!/usr/bin/env python3
"""Regenerate the public open-source PR statistics in a GitHub profile README.

Only merged PRs contribute to the additions/deletions metric; calculations
use GitHub's PR-level diff stats, not unique lines remaining in the repository.
No third-party dependencies; intended for GitHub Actions' GITHUB_TOKEN.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
ASSETS = ROOT / "assets"
USERNAME = "BobSong-dev"
REPOSITORIES = (
    ("Apache ShenYu", "apache/shenyu"),
    ("Checkstyle", "checkstyle/checkstyle"),
    ("Apache SeaTunnel", "apache/seatunnel"),
)
START = "<!-- CONTRIBUTIONS:START -->"
END = "<!-- CONTRIBUTIONS:END -->"
GITHUB_GRAPHQL = "https://api.github.com/graphql"

QUERY = """
query ProfilePublicPRs($searchQuery: String!, $cursor: String) {
  search(query: $searchQuery, type: ISSUE, first: 100, after: $cursor) {
    issueCount
    nodes {
      ... on PullRequest {
        number
        merged
        state
        additions
        deletions
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""


@dataclass(frozen=True)
class Stats:
    name: str
    repo: str
    prs: int
    merged: int
    open: int
    additions: int | None
    deletions: int | None

    @property
    def changes(self) -> int | None:
        if self.additions is None or self.deletions is None:
            return None
        return self.additions + self.deletions


def post_graphql(token: str, search_query: str, cursor: str | None) -> dict:
    payload = json.dumps({"query": QUERY, "variables": {
        "searchQuery": search_query,
        "cursor": cursor,
    }}).encode("utf-8")
    request = urllib.request.Request(
        GITHUB_GRAPHQL,
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "BobSong-dev-profile-stats",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="POST",
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=40) as response:
                body = json.load(response)
            if body.get("errors"):
                raise RuntimeError(f"GitHub GraphQL errors: {body['errors']}")
            if "data" not in body or not body["data"]:
                raise RuntimeError("GitHub GraphQL response missing data")
            return body["data"]
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:500]
            if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise RuntimeError(f"GitHub HTTP {error.code}: {detail}") from error
        except urllib.error.URLError:
            if attempt == 2:
                raise
        time.sleep(2 ** attempt)
    raise RuntimeError("GitHub request failed")


def fetch_repository_prs(token: str, repo: str, username: str = USERNAME) -> list[dict]:
    search_query = f"repo:{repo} author:{username} is:pr"
    all_prs: list[dict] = []
    cursor: str | None = None
    expected_total: int | None = None
    while True:
        search = post_graphql(token, search_query, cursor)["search"]
        expected_total = search["issueCount"]
        nodes = search.get("nodes") or []
        if any(node is None or "merged" not in node for node in nodes):
            raise RuntimeError(f"Unexpected non-PR or inaccessible PR for {repo}")
        all_prs.extend(nodes)
        if len(all_prs) > 1000:
            raise RuntimeError(f"GitHub's 1,000-result search cap exceeded for {repo}")
        page = search["pageInfo"]
        if not page["hasNextPage"]:
            break
        new_cursor = page.get("endCursor")
        if not new_cursor or new_cursor == cursor:
            raise RuntimeError(f"Invalid GitHub pagination cursor for {repo}")
        cursor = new_cursor
    if len(all_prs) != expected_total:
        raise RuntimeError(
            f"Incomplete GitHub search for {repo}: received {len(all_prs)}/{expected_total}"
        )
    if len({p["number"] for p in all_prs}) != len(all_prs):
        raise RuntimeError(f"Duplicate PR numbers returned for {repo}")
    return all_prs


def summarize(name: str, repo: str, prs: list[dict]) -> Stats:
    merged = [pr for pr in prs if pr["merged"]]
    opened = [pr for pr in prs if pr["state"] == "OPEN"]
    return Stats(
        name=name,
        repo=repo,
        prs=len(prs),
        merged=len(merged),
        open=len(opened),
        additions=sum(int(p["additions"]) for p in merged),
        deletions=sum(int(p["deletions"]) for p in merged),
    )


def totals(stats: list[Stats]) -> Stats:
    def sum_optional(field: str) -> int | None:
        values = [getattr(item, field) for item in stats]
        return None if any(value is None for value in values) else sum(values)
    return Stats(
        name="合计",
        repo="",
        prs=sum(s.prs for s in stats),
        merged=sum(s.merged for s in stats),
        open=sum(s.open for s in stats),
        additions=sum_optional("additions"),
        deletions=sum_optional("deletions"),
    )


def fmt(n: int | None) -> str:
    return "待更新" if n is None else f"{n:,}"


def render_region(stats: list[Stats]) -> str:
    rows = []
    for item in (*stats, totals(stats)):
        if item.repo:
            query = urllib.parse.quote(f"is:pr author:{USERNAME}", safe="")
            label = f"[{item.name}](https://github.com/{item.repo}/pulls?q={query})"
        else:
            label = "**合计**"
        changes = (
            "首次运行后更新" if item.changes is None else
            f"+{fmt(item.additions)} / −{fmt(item.deletions)} = **{fmt(item.changes)}**"
        )
        rows.append(f"| {label} | {fmt(item.prs)} | {fmt(item.merged)} | {fmt(item.open)} | {changes} |")
    table = "\n".join(rows)
    return f"""{START}
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="./assets/pr-stats-dark.svg" />
  <source media="(prefers-color-scheme: light)" srcset="./assets/pr-stats-light.svg" />
  <img alt="Apache ShenYu、Checkstyle 与 Apache SeaTunnel 的 Pull Request 统计" src="./assets/pr-stats-light.svg" width="100%" />
</picture>

| 开源项目 | 提交 PR | 已合并 | 待处理 | 已合并 PR 代码变更量* |
|:--|--:|--:|--:|--:|
{table}

<sub>* 代码变更量 = 已合并 PR 的新增行数 + 删除行数之和，不代表最终代码库净新增行数。仅统计上表中的三个公开仓库；数据由 GitHub Actions 每日自动更新。</sub>
{END}"""


def replace_region(original: str, replacement: str) -> str:
    pattern = re.compile(re.escape(START) + r".*?" + re.escape(END), re.DOTALL)
    if len(pattern.findall(original)) != 1:
        raise ValueError("README must have exactly one matching contributions block")
    return pattern.sub(lambda _: replacement, original, count=1)


def make_svg(stats: list[Stats], dark: bool) -> str:
    t = totals(stats)
    bg, panel, stroke, primary, secondary = (
        ("#0D1117", "#161B22", "#30363D", "#F0F6FC", "#8B949E")
        if dark else
        ("#F6F8FA", "#FFFFFF", "#D0D7DE", "#1F2328", "#57606A")
    )
    values = (fmt(t.prs), fmt(t.merged), fmt(t.changes), fmt(len(stats)))
    labels = ("提交 PR", "已合并 PR", "合并代码变更行数", "参与的开源项目")
    accents = ("#58A6FF", "#3FB950", "#D29922", "#A371F7")
    cards: list[str] = []
    for i, (value, label, accent) in enumerate(zip(values, labels, accents)):
        x = 32 + i * 282
        cards.append(f'''<rect x="{x}" y="76" width="266" height="135" rx="13" fill="{panel}" stroke="{stroke}" />
<rect x="{x+17}" y="93" width="3" height="16" rx="1" fill="{accent}" />
<text x="{x+29}" y="105" font-size="13" font-weight="600" letter-spacing="0.4" fill="{secondary}">{escape(label)}</text>
<text x="{x+19}" y="177" font-size="39" font-weight="700" fill="{primary}">{escape(value)}</text>''')
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="253" viewBox="0 0 1200 253" role="img" aria-label="GitHub 开源贡献 PR 统计">
<rect x="0" y="0" width="1200" height="253" rx="18" fill="{bg}" />
<text x="32" y="42" font-size="19" font-weight="700" fill="{primary}" font-family="Noto Sans CJK SC, Microsoft YaHei, PingFang SC, sans-serif">开源贡献  <tspan fill="#58A6FF">/</tspan>  数据概览</text>
<text x="1167" y="42" font-size="12" text-anchor="end" fill="{secondary}" font-family="Noto Sans CJK SC, Microsoft YaHei, PingFang SC, sans-serif">BobSong-dev · GitHub 公开 PR</text>
<g font-family="Noto Sans CJK SC, Microsoft YaHei, PingFang SC, sans-serif">
{''.join(cards)}
<text x="32" y="234" font-size="12" fill="{secondary}">统计范围：Apache ShenYu · Checkstyle · Apache SeaTunnel　｜　变更行数：已合并 PR 的新增 + 删除（未去重）</text>
</g>
</svg>
'''


def update_files(stats: list[Stats], root: Path = ROOT) -> None:
    readme = root / "README.md"
    assets = root / "assets"
    original = readme.read_text(encoding="utf-8")
    new_readme = replace_region(original, render_region(stats))
    dark = make_svg(stats, dark=True)
    light = make_svg(stats, dark=False)
    assets.mkdir(parents=True, exist_ok=True)
    # All remote calls and rendering complete before any local file is touched.
    if new_readme != original:
        readme.write_text(new_readme, encoding="utf-8")
    for filename, contents in (("pr-stats-dark.svg", dark), ("pr-stats-light.svg", light)):
        target = assets / filename
        if not target.exists() or target.read_text(encoding="utf-8") != contents:
            target.write_text(contents, encoding="utf-8")


def main() -> int:
    token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")
    if not token:
        print("GH_TOKEN not set. Run this inside GitHub Actions or supply a read-only token.", file=sys.stderr)
        return 2
    stats = []
    for name, repo in REPOSITORIES:
        pull_requests = fetch_repository_prs(token, repo)
        s = summarize(name, repo, pull_requests)
        stats.append(s)
        print(f"{repo}: {s.prs} PRs; {s.merged} merged; {s.open} open; "
              f"+{fmt(s.additions)} / -{fmt(s.deletions)}")
    update_files(stats)
    print("README and both SVG cards updated successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
