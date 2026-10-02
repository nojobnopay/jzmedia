const PRESETS = {
  deepseek: { base_url: 'https://api.deepseek.com', model: 'deepseek-flash' },
  opencode_go: { base_url: 'https://opencode.ai/zen/go/v1', model: 'glm-5.3-flash' },
}

// Apply only from an explicit provider selection, never while loading saved settings.
export function aiProviderPreset(provider) {
  return { ...(PRESETS[provider] || {}) }
}
