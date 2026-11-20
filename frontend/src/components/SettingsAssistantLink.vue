<template>
  <RouterLink :to="to" custom v-slot="{ href, route, navigate }">
    <a :href="href" @click="follow($event, navigate, route.fullPath)"><slot /></a>
  </RouterLink>
</template>

<script setup>
import { isNavigationFailure, NavigationFailureType, RouterLink, useRouter } from 'vue-router'

const props = defineProps({
  to: { type: Object, required: true }, afterNavigation: { type: Function, required: true },
})
const router = useRouter()
async function follow(event, navigate, destination) {
  if (event.defaultPrevented) return
  // A successful navigation can remove the suggestion containing this link.
  // Keep the panel callback alive until the unsaved-changes guard resolves.
  const arrived = props.afterNavigation
  const navigation = navigate(event)
  if (!event.defaultPrevented) return // A modified click keeps the current tab open.
  const failure = await navigation
  if ((!failure || isNavigationFailure(failure, NavigationFailureType.duplicated)) &&
    router.currentRoute.value.fullPath === destination) arrived()
}
</script>
