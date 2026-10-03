package org.jzmedia.tv.playback

import org.junit.Assert.*
import org.junit.Test

class PlaybackModelsTest {
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
        assertFalse(shouldMarkWatched(5700.0, 6000.0))
        assertFalse(shouldMarkWatched(100.0, 6000.0))
        assertFalse(shouldMarkWatched(0.0, 0.0))
        val state = PlaybackState(position = 5900.0, duration = 6000.0)
        assertFalse(state.ended)
    }

    @Test fun displayTimeHandlesUnknownAndLongMedia() {
        assertEquals("0:00", playbackTime(Double.NaN))
        assertEquals("0:00", playbackTime(-20.0))
        assertEquals("1:02:03", playbackTime(3723.0))
    }
}
