package org.jzmedia.tv.data

import android.content.Context
import java.util.Locale

/** One explicit choice and the number of input letters that it consumes. */
data class OnlinePinyinCandidate(val text: String, val consumed: Int)

/**
 * Offline reverse lookup, not an online title index. The source data contains
 * pronunciation mappings but no word frequencies. See assets/online_pinyin.
 */
class OnlinePinyinDictionary private constructor(private val entries: Map<String, List<String>>) {
    /** Returns choices for complete prefixes; selecting one leaves the suffix untouched. */
    fun candidates(composition: String): List<OnlinePinyinCandidate> {
        val input = composition.lowercase(Locale.ROOT)
        if (input.isEmpty() || input.length > MAX_COMPOSITION || input.any { it !in 'a'..'z' }) return emptyList()
        val groups = (input.length downTo 1).mapNotNull { count ->
            entries[input.substring(0, count)]?.map { OnlinePinyinCandidate(it, count) }
        }
        if (groups.isEmpty()) return emptyList()
        // Interleave chunks so ambiguous prefixes (xian / xi + an) remain reachable.
        val result = ArrayList<OnlinePinyinCandidate>()
        val longest = groups.maxOf { it.size }
        for (offset in 0 until longest step PREFIX_CHUNK) {
            for (group in groups) {
                if (offset < group.size) result.addAll(group.subList(offset, minOf(offset + PREFIX_CHUNK, group.size)))
            }
        }
        return result
    }

    companion object {
        const val MAX_COMPOSITION = 48
        private const val PREFIX_CHUNK = 4

        /** Must be called off the UI thread. Nothing is fetched from the network. */
        fun load(context: Context): OnlinePinyinDictionary = context.assets.open("online_pinyin/dictionary.tsv")
            .bufferedReader(Charsets.UTF_8).use { reader -> fromLines(reader.lineSequence()) }

        fun fromLines(lines: Sequence<String>): OnlinePinyinDictionary {
            val entries = HashMap<String, List<String>>()
            for (line in lines) {
                val delimiter = line.indexOf('\t')
                if (delimiter <= 0 || delimiter == line.lastIndex) continue
                val key = line.substring(0, delimiter)
                if (key.any { it !in 'a'..'z' } || key.length > MAX_COMPOSITION) continue
                entries[key] = line.substring(delimiter + 1).split(' ').filter(String::isNotEmpty).distinct()
            }
            return OnlinePinyinDictionary(entries)
        }
    }
}
