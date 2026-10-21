package org.jzmedia.tv.data

import java.util.concurrent.atomic.AtomicLong
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/** A stopped scan may still own blocking probes, but cannot update a newer scan's UI. */
internal class DiscoveryScan(
    private val scope: CoroutineScope,
    private val scan: suspend ((Int, Int) -> Unit) -> List<DiscoveredServer> = { progress ->
        ServerDiscovery.scan(onProgress = progress)
    },
) {
    private val generation = AtomicLong()
    private var job: Job? = null

    fun cancel() {
        generation.incrementAndGet()
        job?.cancel()
        job = null
    }

    fun start(
        onProgress: (Int, Int) -> Unit,
        onResult: (List<DiscoveredServer>) -> Unit,
        onFailure: () -> Unit,
        onFinished: () -> Unit,
    ): Job {
        cancel()
        val current = generation.get()
        return scope.launch {
            try {
                val found = scan { done, total ->
                    // Probes run on IO; publish on the caller's scope after checking
                    // ownership again, including callbacks already queued at cancellation.
                    scope.launch { if (generation.get() == current) onProgress(done, total) }
                }
                ensureActive()
                if (generation.get() == current) onResult(found)
            } catch (error: CancellationException) { throw error }
            catch (_: Exception) { if (scope.isActive && generation.get() == current) onFailure() }
            finally { if (scope.isActive && generation.get() == current) onFinished() }
        }.also { job = it }
    }
}
