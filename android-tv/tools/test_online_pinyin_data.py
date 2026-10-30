"""Regression tests for the packaged offline reverse pronunciation table."""
import unittest
from pathlib import Path


class OnlinePinyinDataTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1] / "app/src/main/assets/online_pinyin/dictionary.tsv"
        cls.table = dict(line.split("\t", 1) for line in path.read_text(encoding="utf-8").splitlines())

    def test_actual_dictionary_covers_segmented_title_and_polyphonic_chars(self):
        for key, word in (("yu", "予"), ("cang", "苍"), ("long", "龙"),
                          ("zhong", "重"), ("chong", "重"), ("nihao", "你好"),
                          ("nv", "女")):
            self.assertIn(word, self.table[key].split(" "), (key, word))
        self.assertGreater(len(self.table), 40000)

    def test_packaged_licenses_and_corpus_are_present(self):
        notice = (Path(__file__).resolve().parents[1] / "app/src/main/assets/online_pinyin/NOTICE.txt").read_text()
        self.assertIn("pinyin-data", notice)
        self.assertIn("phrase-pinyin-data", notice)
        self.assertIn("Permission is hereby granted", notice)


if __name__ == "__main__":
    unittest.main()
