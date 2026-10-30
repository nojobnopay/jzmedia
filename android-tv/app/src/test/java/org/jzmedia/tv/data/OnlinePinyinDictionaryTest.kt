package org.jzmedia.tv.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class OnlinePinyinDictionaryTest {
    private val dictionary = OnlinePinyinDictionary.fromLines(sequenceOf(
        "yu\t于 鱼 予 与 玉 语",
        "cang\t苍 仓 藏",
        "long\t龙 隆",
        "xian\t先 线 仙 献 县 显 限",
        "xi\t西 希 习 洗",
        "xianren\t仙人",
        "zhong\t中 重",
        "chong\t重 冲",
        "nihao\t你好",
    ))

    @Test fun fullPinyinCanBeSelectedInSegmentsWithoutLosingSuffix() {
        var remaining = "yucanglong"
        var query = ""
        for (wanted in listOf("予", "苍", "龙")) {
            val candidate = dictionary.candidates(remaining).first { it.text == wanted }
            query += candidate.text
            remaining = remaining.drop(candidate.consumed)
        }
        assertEquals("予苍龙", query)
        assertEquals("", remaining)
    }

    @Test fun exactPhraseAndAlternativeReadingsAreAvailable() {
        assertEquals(OnlinePinyinCandidate("你好", 5), dictionary.candidates("NIHAO").first())
        assertTrue(dictionary.candidates("zhong").any { it.text == "重" && it.consumed == 5 })
        assertTrue(dictionary.candidates("chong").any { it.text == "重" && it.consumed == 5 })
    }

    @Test fun ambiguousSyllablesAndLaterPagesRemainReachable() {
        val options = dictionary.candidates("xian")
        assertEquals("先", options.first().text)
        assertTrue(options.take(8).any { it.text == "西" && it.consumed == 2 })
        assertTrue(options.drop(8).any { it.text == "限" })
        assertFalse(options.any { it.text == "仙人" })
    }

    @Test fun invalidAndUnfinishedInputsAreNotConvertedOrTruncated() {
        assertTrue(dictionary.candidates("").isEmpty())
        assertTrue(dictionary.candidates("ni hao").isEmpty())
        assertTrue(dictionary.candidates("x".repeat(49)).isEmpty())
        assertTrue(dictionary.candidates("qqq").isEmpty())
        assertEquals(OnlinePinyinCandidate("你好", 5), dictionary.candidates("nihaoxyz").first())
    }
}
