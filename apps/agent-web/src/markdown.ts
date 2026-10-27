import { Marked } from 'marked'

const numberRange = /([+-]?\d+(?:\.\d+)?%?)[ \t]*(?:\*\*)?(?:\\?~){1,2}(?:\*\*)?[ \t]*([+-]?\d+(?:\.\d+)?%?)/

const answerMarkdown = new Marked({
  extensions: [{
    name: 'numberRange',
    level: 'inline',
    start(source) {
      const index = source.search(numberRange)
      return index >= 0 ? index : undefined
    },
    tokenizer(source) {
      const match = numberRange.exec(source)
      if (match?.index === 0) {
        return { type: 'numberRange', raw: match[0], text: `${match[1]}～${match[2]}` }
      }
    },
    renderer(token) {
      return String(token.text)
    },
  }],
})

export function parseAnswerMarkdown(source: string): string {
  return answerMarkdown.parse(source, { async: false })
}
