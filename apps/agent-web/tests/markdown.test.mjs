import assert from 'node:assert/strict'
import { test } from 'node:test'

import { parseAnswerMarkdown } from '../src/markdown.ts'

test('multiple dosage ranges in one paragraph never become strikethrough', () => {
  const html = parseAnswerMarkdown(
    '- **华法林**：4F-PCC（25~50 IU/kg），对于 INR 1.3~1.9，可考虑 PCC 10~20 IU/kg[1]。',
  )
  assert.doesNotMatch(html, /<del>/)
  for (const range of ['25～50', '1.3～1.9', '10～20']) assert.ok(html.includes(range))
  assert.match(html, /<strong>华法林<\/strong>/)
  assert.ok(html.includes('[1]'))
})

test('escaped and malformed numeric range markers remain ranges', () => {
  for (const source of ['25\\~50', '25~~50', '25**~~**50', '25～50']) {
    assert.equal(parseAnswerMarkdown(source), '<p>25～50</p>\n')
  }
})

test('code samples and intentional strikethrough keep normal Markdown behavior', () => {
  const html = parseAnswerMarkdown('`25~50`\n\n```text\n1.3~1.9\n```\n\n~~旧方案~~')
  assert.match(html, /<code>25~50<\/code>/)
  assert.ok(html.includes('1.3~1.9'))
  assert.match(html, /<del>旧方案<\/del>/)
})
