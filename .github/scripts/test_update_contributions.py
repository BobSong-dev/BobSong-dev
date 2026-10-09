import importlib.util
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

module_path = Path(__file__).with_name("update_contributions.py")
spec = importlib.util.spec_from_file_location("profile_stats", module_path)
stats = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = stats
spec.loader.exec_module(stats)


class ContributionStatsTests(unittest.TestCase):
    def setUp(self):
        self.prs = [
            {"number": 10, "merged": True, "state": "MERGED", "additions": 30, "deletions": 9},
            {"number": 11, "merged": True, "state": "MERGED", "additions": 12, "deletions": 3},
            {"number": 12, "merged": False, "state": "OPEN", "additions": 9000, "deletions": 100},
            {"number": 13, "merged": False, "state": "CLOSED", "additions": 100, "deletions": 100},
        ]

    def test_only_merged_prs_contribute_lines(self):
        s = stats.summarize("Apache ShenYu", "apache/shenyu", self.prs)
        self.assertEqual((s.prs, s.merged, s.open, s.additions, s.deletions, s.changes),
                         (4, 2, 1, 42, 12, 54))

    def test_render_and_replace_section_without_touching_other_content(self):
        r = [stats.summarize("Apache ShenYu", "apache/shenyu", self.prs)]
        original = "Before\n" + stats.START + "\nold text\n" + stats.END + "\nAfter\n"
        changed = stats.replace_region(original, stats.render_region(r))
        self.assertTrue(changed.startswith("Before\n"))
        self.assertTrue(changed.endswith("\nAfter\n"))
        self.assertIn("+42 / −12 = **54**", changed)
        self.assertIn("| 4 | 2 | 1 |", changed)
        self.assertEqual(changed, stats.replace_region(changed, stats.render_region(r)))
        self.assertIn("| 开源项目 | 提交 PR | 已合并 | 待处理 |", changed)

    def test_fails_if_markers_not_exact(self):
        with self.assertRaises(ValueError):
            stats.replace_region("no markers", stats.render_region([]))

    def test_svg_is_valid_and_contains_metrics(self):
        r = [stats.summarize("Apache ShenYu", "apache/shenyu", self.prs)]
        for dark in (True, False):
            svg = stats.make_svg(r, dark)
            root = ET.fromstring(svg)
            self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg")
            self.assertIn("54", svg)
            self.assertIn("合并代码变更行数", svg)

    def test_atomic_workflow_data_write_and_idempotency(self):
        r = [stats.summarize("Apache ShenYu", "apache/shenyu", self.prs)]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text(stats.START + "\nold\n" + stats.END, encoding="utf-8")
            stats.update_files(r, root)
            first = (root / "README.md").read_bytes()
            self.assertTrue((root / "assets" / "pr-stats-light.svg").exists())
            stats.update_files(r, root)
            self.assertEqual(first, (root / "README.md").read_bytes())

    def test_pagination_completeness(self):
        p1 = {"search": {"issueCount": 2, "nodes": [self.prs[0]],
                         "pageInfo": {"hasNextPage": True, "endCursor": "cursor1"}}}
        p2 = {"search": {"issueCount": 2, "nodes": [self.prs[1]],
                         "pageInfo": {"hasNextPage": False, "endCursor": "cursor2"}}}
        with patch.object(stats, "post_graphql", side_effect=[p1, p2]) as mock:
            items = stats.fetch_repository_prs("stub", "apache/shenyu")
        self.assertEqual(len(items), 2)
        self.assertEqual(mock.call_count, 2)
        self.assertEqual(mock.call_args_list[1].args[2], "cursor1")

    def test_incomplete_response_is_rejected(self):
        wrong = {"search": {"issueCount": 4, "nodes": [self.prs[0]],
                            "pageInfo": {"hasNextPage": False, "endCursor": "x"}}}
        with patch.object(stats, "post_graphql", return_value=wrong):
            with self.assertRaises(RuntimeError):
                stats.fetch_repository_prs("stub", "apache/shenyu")


if __name__ == "__main__":
    unittest.main()
