package org.jzmedia.tv.ui

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Deferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.async
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext

/** One request per key, cancelled as soon as its last visible consumer leaves. */
internal class SharedImageLoads<K, V>(
    private val scope: CoroutineScope = CoroutineScope(SupervisorJob() + Dispatchers.IO),
) {
    private class Entry<V>(val task: Deferred<V>, var users: Int = 0)
    private val lock = Mutex()
    private val entries = mutableMapOf<K, Entry<V>>()

    suspend fun get(key: K, load: suspend () -> V): V {
        val entry = lock.withLock {
            entries.getOrPut(key) { Entry(scope.async(start = CoroutineStart.LAZY) { load() }) }
                .also { it.users++ }
        }
        try {
            return entry.task.await()
        } finally {
            withContext(NonCancellable) {
                lock.withLock {
                    entry.users--
                    if (entry.users == 0) {
                        if (entries[key] === entry) entries.remove(key)
                        entry.task.cancel()
                    }
                }
            }
        }
    }
}
