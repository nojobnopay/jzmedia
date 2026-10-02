// Shared by Node indexing and the theme's static browser import. Never serialize
// this function into executable metadata: that would require CSP unsafe-eval.
export function tokenize(text) {
  const segmenter = new Intl.Segmenter('zh-CN', { granularity: 'word' })
  return [...segmenter.segment(String(text).normalize('NFKC').toLowerCase())]
    .filter(part => part.isWordLike).map(part => part.segment)
}

export const searchOptions = {
  combineWith: 'AND', prefix: true, fuzzy: false,
  boost: { title: 4, text: 2, titles: 1 },
}

export function renderSearchContent(source, env, md) {
  const html = md.render(source, env)
  if (env.frontmatter?.search === false) return ''
  // Component attributes are not text nodes in markdown-it's output. Include
  // labels as searchable prose while leaving slot Markdown and heading IDs intact.
  return html.replace(/<(DocStep|DocFigure|DocVideo|DocDiagram)\b([^>]*)\/?\s*>/g, (_tag, name, attrs) => {
    const labels = [...attrs.matchAll(/\b(?:title|caption|alt)="([^"]*)"/g)].map(match => match[1])
    return `<div>${labels.join(' ')}</div>`
  }).replace(/<\/Doc(?:Step|Figure|Video|Diagram)>/g, '')
}
