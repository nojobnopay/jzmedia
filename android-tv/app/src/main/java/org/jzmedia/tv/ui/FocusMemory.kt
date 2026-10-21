package org.jzmedia.tv.ui

/** Saved focus survives the transient navigation focus emitted while a page leaves. */
class FocusMemory(initial: String) {
    var key = initial
    val restoreKey = initial
    var restored = false
    var leaving = false

    fun focused(target: String) { if (!leaving) key = target }
    fun save(): String = key

    fun restoreFocus(target: String, request: () -> Boolean): Boolean {
        if (restored || leaving || restoreKey != target) return false
        return request().also { if (it) restored = true }
    }

    companion object {
        fun restore(saved: String): FocusMemory = FocusMemory(saved)
    }
}
