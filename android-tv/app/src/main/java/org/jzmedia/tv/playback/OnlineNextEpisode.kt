package org.jzmedia.tv.playback

import org.jzmedia.tv.data.OnlineApi

internal data class OnlineNextEpisode(val request: OnlinePlaybackRequest, val requiresNetwork: Boolean)

/** A cache snapshot names the adjacent episode without contacting a provider or skipping a gap. */
internal suspend fun resolveOnlineNext(online: OnlineApi, current: OnlinePlaybackRequest): OnlineNextEpisode? {
    val cached = if (current.localCache) online.cacheDetail(current.identity.itemId)
        else online.cacheLookup(current.identity.sourceId, current.externalId)
    val detail = cached?.detail ?: online.detail(current.identity.sourceId, current.externalId)
    val entry = detail.nextEpisode(current.identity) ?: return null
    val identity = entry.identity ?: return null
    val ready = cached?.isReady(identity) == true
    val saved = online.playbackState(identity)
    val progress = saved?.selectedProgress
    return OnlineNextEpisode(OnlinePlaybackRequest(identity, detail.item.title + " · " + entry.label,
        cached?.externalId ?: current.externalId, if (progress?.completed == false) progress.position else 0.0,
        saved?.scopeToken.orEmpty(), progress?.writerRevision ?: 0, autoplay = current.autoplay, localCache = ready),
        requiresNetwork = cached != null && !ready)
}
