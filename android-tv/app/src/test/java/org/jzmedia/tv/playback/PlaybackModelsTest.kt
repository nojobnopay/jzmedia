package org.jzmedia.tv.playback

import org.junit.Assert.*
import org.junit.Test

class PlaybackModelsTest {
    @Test fun phoneHandoffUsesItsExplicitTimeAndTracksWithoutResumeHeuristics() {
        val request = PlaybackRequest("movie", 42, "test", position = 5.0, audio = 1, subtitle = -1, remoteSessionId = "tv_one")
        assertEquals(5.0, explicitPlaybackStart(request, 600.0)!!, 0.0)
        val selection = handoffTrackSelection(request, 2, 3, ContinuationSelection(0, 0))
        assertEquals(1, selection.audio)
        assertNull(selection.subtitle)
        assertEquals(0.0, explicitPlaybackStart(request.copy(position = 0.0), 600.0)!!, 0.0)
        assertEquals(599.9, explicitPlaybackStart(request.copy(position = 900.0), 600.0)!!, .001)
        assertNull(explicitPlaybackStart(request.copy(position = null), 600.0))
        assertThrows(IllegalArgumentException::class.java) { handoffTrackSelection(request.copy(audio = 2), 2, 3, selection) }
        assertThrows(IllegalArgumentException::class.java) { handoffTrackSelection(request.copy(subtitle = 3), 2, 3, selection) }
    }

    @Test fun remoteImageSubtitlesNeverReachTheServerBurnDecision() {
        val image = MediaTrack(0, "PGS", image = true)
        val text = MediaTrack(1, "ASS")
        val request = PlaybackRequest("movie", 42, "test", remoteSessionId = "tv_one")
        assertNull(serverSubtitleSelection(request, listOf(image, text), 0))
        assertEquals(1, serverSubtitleSelection(request, listOf(image, text), 1))
        assertEquals(0, serverSubtitleSelection(request.copy(remoteSessionId = null), listOf(image, text), 0))
        assertEquals(1, nativeSubtitleCandidateIndex(listOf(true, true), 1, 2))
        assertNull(nativeSubtitleCandidateIndex(listOf(true), 1, 2))
        assertNull(nativeSubtitleCandidateIndex(listOf(true, false), 1, 2))
    }
    @Test fun matroskaTrackNumbersNeverMatchFfprobeIndices() {
        // ffprobe audio streams 1/2 commonly have Matroska TrackNumbers 2/3.
        val audio = listOf(AudioCandidate("2", "中文", true), AudioCandidate("3", "英语", true))
        assertEquals(1, audioCandidateIndex(audio, 1, 2, direct = true, singleAudio = false))
        assertNull(audioCandidateIndex(audio.take(1), 1, 2, direct = true, singleAudio = false))
    }

    @Test fun hlsNamesAllowReorderedRenditionsAndTsUsesItsSingleSelectedTrack() {
        val audio = listOf(AudioCandidate("a", "audio2", true), AudioCandidate("b", "audio1", true))
        assertEquals(0, audioCandidateIndex(audio, 1, 2, direct = false, singleAudio = false))
        assertEquals(0, audioCandidateIndex(audio.take(1), 1, 2, direct = false, singleAudio = true))
        assertNull(audioCandidateIndex(listOf(AudioCandidate("a", "audio1", false)), 0, 1, direct = false, singleAudio = false))
    }

    @Test fun sessionOffsetIsNotInitialSeekTime() {
        // A reused start=0 cache and a keyframe-aligned segment reach the same source time.
        assertEquals(123.0, sourcePosition(123000, 0.0, 600.0), .001)
        assertEquals(123.0, sourcePosition(7000, 116.0, 600.0), .001)
        assertEquals(600.0, sourcePosition(900000, 116.0, 600.0), .001)
        assertEquals(0.0, sourcePosition(-50, 0.0, 0.0), .001)
    }

    @Test fun progressNearEndDoesNotRepresentPlaybackEnded() {
        assertTrue(shouldMarkWatched(5701.0, 6000.0))
        assertTrue(shouldMarkWatched(5700.0, 6000.0))
        assertFalse(shouldMarkWatched(5699.0, 6000.0))
        assertFalse(shouldMarkWatched(100.0, 6000.0))
        assertFalse(shouldMarkWatched(0.0, 0.0))
        val state = PlaybackState(position = 5900.0, duration = 6000.0)
        assertFalse(state.ended)
    }

    @Test fun shortMediaNeedsTheSameMinimumWatchedRatioAsOtherClients() {
        assertFalse(shouldMarkWatched(0.0, 180.0))
        assertFalse(shouldMarkWatched(10.0, 180.0))
        assertFalse(shouldMarkWatched(143.9, 180.0))
        assertTrue(shouldMarkWatched(144.0, 180.0))
        assertFalse(shouldMarkWatched(800.0, 1200.0))
        assertTrue(shouldMarkWatched(960.0, 1200.0))
        assertFalse(shouldMarkWatched(4800.0, 6000.0))
        assertTrue(shouldMarkWatched(5700.0, 6000.0))
        assertTrue(shouldMarkWatched(180.0, 180.0))
        assertTrue(shouldMarkWatched(181.0, 180.0))
    }

    @Test fun invalidCompletionInputsNeverMarkWatched() {
        listOf(-1.0, Double.NaN, Double.POSITIVE_INFINITY, Double.NEGATIVE_INFINITY).forEach {
            assertFalse(shouldMarkWatched(it, 180.0))
            assertFalse(shouldMarkWatched(150.0, it))
        }
        assertFalse(shouldMarkWatched(150.0, 0.0))
    }

    @Test fun displayTimeHandlesUnknownAndLongMedia() {
        assertEquals("0:00", playbackTime(Double.NaN))
        assertEquals("0:00", playbackTime(-20.0))
        assertEquals("1:02:03", playbackTime(3723.0))
    }

    @Test fun remoteSeekStartsPreciseAcceleratesOnHoldAndStopsAtBothEnds() {
        assertEquals(10.0, seekStep(0), .001)
        assertEquals(30.0, seekStep(8), .001)
        assertEquals(120.0, seekStep(20), .001)
        assertEquals(300.0, seekStep(0, 300), .001)
        assertEquals(600.0, seekStep(8, 300), .001)
        assertEquals(3610.0, seekPreviewPosition(3600.0, 7200.0, 1), .001)
        assertEquals(3570.0, seekPreviewPosition(3600.0, 7200.0, -1, 8), .001)
        assertEquals(3720.0, seekPreviewPosition(3600.0, 7200.0, 1, 20), .001)
        assertEquals(3900.0, seekPreviewPosition(3600.0, 7200.0, 1, 0, 300), .001)
        assertEquals(0.0, seekPreviewPosition(5.0, 7200.0, -1), .001)
        assertEquals(7200.0, seekPreviewPosition(7199.0, 7200.0, 1), .001)
        assertEquals(0.0, seekPreviewPosition(Double.NaN, 0.0, 1), .001)
    }
}
