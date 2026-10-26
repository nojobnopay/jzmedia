package org.jzmedia.tv.data

import android.content.Context
import android.os.Build
import android.provider.Settings

/** Use the TV's user-facing system name when its settings provider exposes it. */
internal fun tvCastReceiverName(context: Context): String = tvCastReceiverName(Build.MODEL) {
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N_MR1) {
        Settings.Global.getString(context.contentResolver, Settings.Global.DEVICE_NAME)
    } else null
}

internal fun tvCastReceiverName(model: String?, readSystemName: () -> String?): String {
    // Some TV vendors omit or restrict the public setting. Naming must never
    // prevent the foreground receiver from registering on those devices.
    val systemName = try { readSystemName() } catch (_: RuntimeException) { null }
    val name = systemName?.trim()?.takeIf { it.isNotEmpty() }
        ?: model?.trim()?.takeIf { it.isNotEmpty() } ?: "电视"
    val suffix = " · jzmedia-tv"
    return name.take(80 - suffix.length) + suffix
}
