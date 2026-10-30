#!/usr/bin/env python3
"""Generate the TV offline reverse-pinyin data from pinned pypinyin 0.55.0.

Run with the project's requirements installed; no network access is used.
The hashes fix the actual distributed dictionaries, not just the package version.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import itertools
import json
import unicodedata
from collections import defaultdict
from pathlib import Path

import pypinyin

EXPECTED = {
    "pinyin_dict.json": "5f294c01e6c6c0a1c8e329c79335a3f8e0b27d06bf1de7a99244b765892d1e5b",
    "phrases_dict.json": "a45ff140a6b631ca9c82127b280a2f414e0aba6bb2824a0e9d1e77fff359c665",
}
ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "app/src/main/assets/online_pinyin/dictionary.tsv"


def normal(s: str) -> str:
    # Decompose tone marks but preserve the distinct ü vowel using keyboard v.
    s = s.replace("ü", "v").replace("ǖ", "v").replace("ǘ", "v").replace("ǚ", "v").replace("ǜ", "v")
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if not unicodedata.combining(c))


def is_hanzi(s: str) -> bool:
    return all("CJK UNIFIED IDEOGRAPH" in unicodedata.name(c, "") or c == "〇" for c in s)


def rank(word: str) -> tuple:
    # GB 2312 level-one characters are a modest approximation, not a frequency model.
    try:
        data = word.encode("gb2312")
        level = 0 if all(data[i] < 0xD8 for i in range(0, len(data), 2)) else 1
        return (level, data)
    except UnicodeEncodeError:
        return (2, word.encode("utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="verify existing output without changing it")
    args = parser.parse_args()
    if importlib.metadata.version("pypinyin") != "0.55.0":
        raise ValueError("Expected pypinyin 0.55.0")
    package = Path(pypinyin.__file__).parent
    raw = {}
    for name, expected in EXPECTED.items():
        data = (package / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError(f"Unexpected source dictionary: {name}")
        raw[name] = json.loads(data)

    entries: dict[str, dict[str, int]] = defaultdict(dict)
    for code, readings in raw["pinyin_dict.json"].items():
        char = chr(int(code))
        if not is_hanzi(char):
            continue
        for index, reading in enumerate(readings.split(",")):
            key = normal(reading)
            if key.isascii() and key.isalpha():
                priority = 1 if index == 0 else 2
                entries[key][char] = min(priority, entries[key].get(char, priority))
    for phrase, readings in raw["phrases_dict.json"].items():
        if not 2 <= len(phrase) <= 8 or not is_hanzi(phrase) or len(readings) != len(phrase):
            continue
        # Rare alternative readings are retained; the upstream data has at most 2 combinations.
        for pieces in itertools.product(*readings):
            key = normal("".join(pieces))
            if key.isascii() and key.isalpha() and len(key) <= 48:
                entries[key][phrase] = 0
    lines = []
    for key in sorted(entries):
        words = sorted(entries[key], key=lambda word: (0 if len(word) > 1 else 1, rank(word)[0], entries[key][word], rank(word)[1]))
        lines.append(key + "\t" + " ".join(words) + "\n")
    output = "".join(lines).encode("utf-8")
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_bytes() != output:
            raise ValueError("Generated dictionary differs from committed asset")
    else:
        OUTPUT.write_bytes(output)
    print(f"{len(entries)} keys, {sum(map(len, entries.values()))} entries, {len(output)} bytes")
    print("sha256", hashlib.sha256(output).hexdigest())


if __name__ == "__main__":
    main()
