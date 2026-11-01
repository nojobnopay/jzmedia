import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { api } from './api.js'
import { useTvIngestFlow } from './useTvIngestFlow.js'
import { ingestionMetadataResults, ingestionMetadataText } from './tvIngestFeedback.js'

export function useTvUpload({ kind, libraryId, queue, done }) {
  const targetShow = ref(0)
  const targetTitle = ref('')
  const targetSeason = ref(1)
  const availableShows = ref([])
  const showsError = ref('')
  const handedOff = ref(false)
  const ingest = useTvIngestFlow({ scopeKey: () => libraryId.value, onCompleted: summary => {
    for (const result of ingestionMetadataResults(summary)) tvResults.value[result.show_id] = ingestionMetadataText(result)
    done()
  } })
  const tvBusy = ingest.busy
  const tvMessage = ingest.message
  const tvReviewing = computed(() => handedOff.value && Boolean(ingest.flow.review.value || ingest.flow.queue.value.length))
  const tvResults = ref({})
  const uploadedShows = computed(() => [...new Map(queue.value.filter(t => t.showId).map(t =>
    [t.showId, { id: t.showId, title: t.showTitle, status: tvResults.value[t.showId] }])).values()])
  let disposed = false
  let generation = 0
  async function loadShows() {
    const current = ++generation
    availableShows.value = []
    targetShow.value = 0
    showsError.value = ''
    if (kind !== 'tv' || !libraryId.value) return
    try {
      for (let offset = 0; ; offset += 200) {
        const d = await api(`/api/tv/shows?library=${libraryId.value}&limit=200&offset=${offset}`)
        if (disposed || current !== generation) return
        availableShows.value.push(...(d.items || []))
        if (!d.has_more || !d.items?.length) break
      }
    } catch (e) { if (current === generation) showsError.value = '剧集列表加载失败：' + e.message }
  }
  watch(libraryId, loadShows)
  async function checkUploadedShows(automatic = true) {
    if (disposed || !uploadedShows.value.length) return
    return ingest.start({ ids: uploadedShows.value.map(show => Number(show.id)) }, {
      automatic, source: 'upload', handoff: () => { handedOff.value = true },
    })
  }
  async function previewTv(id) {
    return ingest.start({ ids: [Number(id)] }, { handoff: () => { handedOff.value = true } })
  }
  onMounted(loadShows)
  onUnmounted(() => { disposed = true; generation++; handedOff.value = false })
  return { targetShow, targetTitle, targetSeason, availableShows, showsError, tvBusy, tvMessage,
    tvReviewing, uploadedShows, checkUploadedShows, previewTv }
}
