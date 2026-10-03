package org.jzmedia.tv.playback

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class PlaybackContinuityTest {
    private fun track(index: Int, language: String = "eng", title: String = "", forced: Boolean = false,
                      image: Boolean = false, codec: String = "aac", channels: Int = 2) =
        MediaTrack(index, "Track $index", image = image, language = language, title = title,
            codec = codec, channels = channels, forced = forced)

    @Test fun reorderedTracksUseLanguageAndAttributesInsteadOfTheOldOrdinal() {
        val previous = track(0, "eng", "Original", channels = 6)
        val tracks = listOf(track(0, "jpn"), track(1, "en", "Original", channels = 2), track(2, "en", "Original", channels = 6))
        assertEquals(2, preferredTrack(tracks, previous))
    }

    @Test fun commentaryAndForcedTracksDoNotReplaceNormalLanguageTracks() {
        assertEquals(2, preferredTrack(listOf(track(0, "eng", "Commentary"), track(1, forced = true), track(2)), track(8)))
        assertNull(preferredTrack(listOf(track(0, "eng", "Director Commentary")), track(1)))
        assertNull(preferredTrack(listOf(track(0, forced = true)), track(1)))
    }

    @Test fun subtitlesCanChangeCodecWithoutChangingLanguage() {
        val preference = track(3, "chi", "中文", image = true, codec = "pgs")
        val tracks = listOf(track(0, "eng", image = false, codec = "subrip"), track(1, "zh-CN", "中文", codec = "subrip"))
        assertEquals(1, preferredTrack(tracks, preference))
    }

    @Test fun unlabeledLanguagesNeedAMatchingTitleAndNeverReuseAnIndex() {
        assertEquals(4, preferredTrack(listOf(track(4, "und", "国语")), track(1, "", "国语")))
        assertNull(preferredTrack(listOf(track(0, "und")), track(0, "und")))
        assertNull(preferredTrack(listOf(track(0, "jpn")), track(0, "eng")))
    }

    @Test fun explicitSubtitleOffSurvivesANewEpisodesDefaultSubtitle() {
        val selection = continuationSelection(listOf(track(0)), listOf(track(0, "chi")), 0, 0,
            ContinuationPreferences(rate = 1.5f, audio = track(0), subtitlesEnabled = false))
        assertNull(selection.subtitle)
        assertNull(selection.notice)
    }

    @Test fun nextRequestSnapshotsTheCurrentRateAndTrackPreferences() {
        val audio = track(2, "chi")
        val subtitle = track(4, "chi", "双语", codec = "subrip")
        val state = PlaybackState(rate = 1.5f, audios = listOf(audio), audio = 2,
            subtitles = listOf(subtitle), subtitle = 4, next = PlaybackRequest("episode", 8L, "第8集", false))
        val next = nextRequestWithPreferences(state)!!
        assertEquals(8L, next.id)
        assertFalse(next.resume)
        assertEquals(1.5f, next.continuation!!.rate, .0001f)
        assertEquals(audio, next.continuation!!.audio)
        assertEquals(subtitle, next.continuation!!.subtitle)
        assertTrue(next.continuation!!.subtitlesEnabled)
        assertFalse(nextRequestWithPreferences(state.copy(subtitle = null))!!.continuation!!.subtitlesEnabled)
        assertNull(nextRequestWithPreferences(state.copy(next = null)))
    }

    @Test fun absentPreferredTracksFallBackExplicitlyWithoutChoosingAnotherSubtitleLanguage() {
        val selection = continuationSelection(listOf(track(0, "jpn")), listOf(track(0, "eng")), 0, 0,
            ContinuationPreferences(audio = track(2, "eng"), subtitlesEnabled = true, subtitle = track(3, "chi")))
        assertEquals(0, selection.audio)
        assertNull(selection.subtitle)
        assertTrue(selection.notice!!.contains("默认音轨"))
        assertTrue(selection.notice!!.contains("已关闭字幕"))
        assertEquals(ContinuationSelection(0, 0), continuationSelection(listOf(track(0)), listOf(track(0)), 0, 0, null))
    }

    @Test fun unavailableNextIsNotSkippedOrTurnedIntoAPlayRequest() {
        val next = JSONObject("""{"id":2,"season":1,"episode":2,"title":"第二集"}""")
        assertEquals(2L, nextPlaybackRequest(next)!!.id) // Old servers without an existence flag remain compatible.
        assertNull(nextPlaybackRequest(JSONObject(next.toString()).put("exists", false)))
        assertNull(nextPlaybackRequest(JSONObject(next.toString()).put("missing", 1)))
        assertNull(nextPlaybackRequest(JSONObject(next.toString()).put("id", 0)))
        assertNull(nextPlaybackRequest(null))
    }

    @Test fun pauseDuringPreparationSurvivesTheFirstRealHlsTimeline() {
        val intent = PlaybackIntent()
        assertTrue(intent.wantsPlay)
        assertFalse(intent.playerShouldPlay)
        intent.request(false)
        intent.observePlayer(false)
        intent.prepared()
        assertFalse(intent.playerShouldPlay)
    }

    @Test fun pausedSeekAndQualityChangesPreserveIntentButAnExplicitPlayCanResume() {
        val intent = PlaybackIntent()
        intent.prepared()
        intent.observePlayer(false)
        assertFalse(intent.playerShouldPlay)
        intent.beginPreparation()
        intent.observePlayer(false)
        intent.prepared()
        assertFalse(intent.playerShouldPlay)
        intent.request(true)
        assertTrue(intent.playerShouldPlay)
    }

    @Test fun internalPreparationPauseDoesNotCancelTheUsersPlayRequest() {
        val intent = PlaybackIntent()
        intent.prepared()
        intent.beginPreparation()
        intent.observePlayer(false)
        assertTrue(intent.wantsPlay)
        assertFalse(intent.playerShouldPlay)
        intent.prepared()
        assertTrue(intent.playerShouldPlay)
    }

    @Test fun menuAndSingleCancellationBlockOnlyTheCurrentCountdown() {
        assertTrue(shouldCountDownNext(true, true, true, false, false, false))
        assertFalse(shouldCountDownNext(true, true, true, true, false, false))
        assertFalse(shouldCountDownNext(true, true, true, false, true, false))
        assertFalse(shouldCountDownNext(false, true, true, false, false, false))
        assertFalse(shouldCountDownNext(true, false, true, false, false, false))
        assertFalse(shouldCountDownNext(true, true, false, false, false, false))
        assertFalse(shouldCountDownNext(true, true, true, false, false, true))
        // A later episode can count down again without changing the saved auto-next preference.
        assertTrue(shouldCountDownNext(true, true, true, false, false, false))
    }
}
