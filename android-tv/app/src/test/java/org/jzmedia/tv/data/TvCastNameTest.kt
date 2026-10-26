package org.jzmedia.tv.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class TvCastNameTest {
    @Test fun userFacingSystemNameTakesPriorityOverOpaqueHardwareModel() {
        assertEquals("主卧小米电视 · jzmedia-tv", tvCastReceiverName("MiTV-MFTP0") { " 主卧小米电视 " })
    }

    @Test fun missingOrRestrictedSystemNameStillAllowsReceiverRegistration() {
        listOf(null, "", "  ").forEach { name ->
            assertEquals("MiTV-MFTP0 · jzmedia-tv", tvCastReceiverName("MiTV-MFTP0") { name })
        }
        assertEquals("MiTV-MFTP0 · jzmedia-tv", tvCastReceiverName("MiTV-MFTP0") { throw SecurityException("Restricted") })
        assertEquals("MiTV-MFTP0 · jzmedia-tv", tvCastReceiverName("MiTV-MFTP0") { throw IllegalStateException("No settings provider") })
        assertEquals("电视 · jzmedia-tv", tvCastReceiverName(null) { null })
    }

    @Test fun longDeviceNamesKeepTheReceiverTypeWithinTheRegistrationLimit() {
        val name = tvCastReceiverName("MiTV-MFTP0") { "主卧电视".repeat(30) }
        assertEquals(80, name.length)
        assertTrue(name.endsWith(" · jzmedia-tv"))
    }
}
