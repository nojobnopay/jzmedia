"""File-level range labels must not overwrite named episodes or single titles."""
import pytest

from app.episode_titles import episode_display_title


@pytest.mark.parametrize("title", [
    None, "", "  ", "第 1 集", "第1话", "第1話", "Episode 1", "EPISODE 01",
    "第1-2集", "第 1 – 3 集", "第1至第2集", "Episodes 1-2", "Episode 1~Episode 2",
])
def test_generic_titles_use_actual_file_range(title):
    assert episode_display_title(title, 1, 2) == "第1-2集"
    assert episode_display_title(title, 3, 4) == "第3-4集"


@pytest.mark.parametrize("title", [
    "初次交锋", "第一话", "第1集：初次交锋", "Episode 1: First Contact", "第1-2集幕后",
])
def test_story_titles_keep_their_original_text(title):
    assert episode_display_title(title, 1, 2) == title


@pytest.mark.parametrize("episode,end", [(1, 0), (1, None), (1, 1), (2, 1), (0, 2),
                                           (None, 2), ("unknown", 2), (1, "unknown"), (1, 10000)])
def test_single_or_invalid_ranges_preserve_original_title(episode, end):
    assert episode_display_title("第 1 集", episode, end) == "第 1 集"
    assert episode_display_title("", episode, end) == ""


def test_numeric_api_fields_can_be_strings():
    assert episode_display_title("第 5 集", "5", "6") == "第5-6集"
