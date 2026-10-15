package org.jzmedia.tv.ui

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test

class ContentVisibilityTest {
    @Test fun privateSelectionExcludesBothOtherModesDespiteRetainedParentalDecision() {
        for (children in listOf("auto", "allow", "block")) {
            assertEquals(ContentVisibilitySummary("不显示", "不显示", "显示"),
                contentVisibilitySummary(true, children, "allow"))
        }
    }

    @Test fun normalSelectionCanAppearInBothNormalAndChildrenButNeverPrivate() {
        assertEquals(ContentVisibilitySummary("显示", "显示", "不显示"),
            contentVisibilitySummary(false, "allow", "block"))
        assertEquals(ContentVisibilitySummary("显示", "不显示", "不显示"),
            contentVisibilitySummary(false, "block", "allow"))
        assertEquals(ContentVisibilitySummary("显示", "显示", "不显示"),
            contentVisibilitySummary(false, "auto", "allow"))
        assertEquals(ContentVisibilitySummary("显示", "不显示", "不显示"),
            contentVisibilitySummary(false, "auto", "block"))
        assertEquals(ContentVisibilitySummary("显示", "不显示（资料不足）", "不显示"),
            contentVisibilitySummary(false, "auto", "unknown"))
    }

    @Test fun movingIntoPrivateDoesNotOverwritePriorChildrenDecision() {
        for (children in listOf("auto", "allow", "block")) {
            val patch = contentVisibilityPatch("show", 17, true, children)
            assertEquals(true, patch.getBoolean("is_private"))
            assertFalse(patch.has("children_override"))
            assertEquals("show", patch.getString("kind"))
            assertEquals(17L, patch.getJSONArray("ids").getLong(0))
        }
    }

    @Test fun movingBackToNormalIncludesTheVisibleChildrenChoice() {
        for (children in listOf("auto", "allow", "block")) {
            val patch = contentVisibilityPatch("movie", 8, false, children)
            assertEquals(false, patch.getBoolean("is_private"))
            assertEquals(children, patch.getString("children_override"))
        }
    }
}
