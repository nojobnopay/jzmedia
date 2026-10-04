import { parseFragment } from 'parse5'

function* htmlFragments(tokens) {
  for (const token of tokens) {
    if (token.type === 'html_block') yield { content: token.content, line: token.lineNumber }
    if (token.type !== 'inline') continue
    let cursor = 0
    for (const child of token.children ?? []) {
      const offset = token.content.indexOf(child.content, cursor)
      if (offset >= 0) cursor = offset + child.content.length
      if (child.type !== 'html_inline') continue
      yield { content: child.content, line: token.lineNumber + token.content.slice(0, offset).split('\n').length - 1 }
    }
  }
}

function* elements(node) {
  if (node.tagName) yield node
  for (const child of node.childNodes ?? []) yield* elements(child)
  if (node.content) yield* elements(node.content)
}

// Use parsed blocks so examples in code fences/quotes do not become real steps.
export default [
  {
    names: ['JZ001', 'task-step-order'],
    description: 'Step 标题使用 H3，并在每个 H2 任务内从 1 连续编号',
    tags: ['headings', 'steps'],
    parser: 'markdownit',
    function(params, onError) {
      let previous = 0
      const tokens = params.parsers.markdownit.tokens
      for (let i = 0; i < tokens.length; i++) {
        const token = tokens[i]
        if (token.type !== 'heading_open' || token.level !== 0) continue
        if (token.tag === 'h1' || token.tag === 'h2') previous = 0
        const match = /^Step\s+(\d+)\b(.*)$/i.exec(tokens[i + 1]?.content ?? '')
        if (!match) continue
        const number = Number(match[1])
        if (!/^\s*[：:]\s*\S/.test(match[2])) {
          onError({ lineNumber: token.lineNumber, detail: '步骤标题使用 Step N：动作，动作不能为空' })
        } else if (token.tag !== 'h3' || number !== previous + 1) {
          onError({ lineNumber: token.lineNumber,
            detail: `应为 H3 的 Step ${previous + 1}，实际为 ${token.tag} 的 Step ${number}` })
        }
        previous = number
      }
    },
  },
  {
    names: ['JZ002', 'step-title-in-markdown'],
    description: 'DocStep 的编号与标题写入 Markdown，不放在组件属性中',
    tags: ['headings', 'steps'],
    parser: 'markdownit',
    function(params, onError) {
      for (const fragment of htmlFragments(params.parsers.markdownit.tokens)) {
        // HTML parsing excludes comments and attribute text that only looks like a tag.
        for (const element of elements(parseFragment(fragment.content, { sourceCodeLocationInfo: true }))) {
          if (element.tagName === 'docstep' && element.attrs.some(attr => /^(?:(?:v-bind)?:)?(?:title|number)$/.test(attr.name))) {
            onError({ lineNumber: fragment.line + element.sourceCodeLocation.startLine - 1,
              detail: '在 <DocStep> 前使用 ### Step N：动作，组件仅负责布局' })
          }
        }
      }
    },
  },
]
