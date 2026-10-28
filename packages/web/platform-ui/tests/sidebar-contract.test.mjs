import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const applications = ['knowledge', 'agent', 'evaluation']

test('each frontend delegates its platform chrome to PlatformSidebar', () => {
  for (const application of applications) {
    const source = readFileSync(`apps/${application}-web/src/App.vue`, 'utf8')
    assert.match(source, /import \{ PlatformSidebar \} from '@medical-rag\/platform-ui'/)
    assert.match(source, new RegExp(`active-app="${application}"`))
    assert.doesNotMatch(source, /class="platform-switcher"/)
  }
})

test('the shared sidebar owns all platform destinations and its business slot', () => {
  const source = readFileSync('packages/web/platform-ui/src/PlatformSidebar.vue', 'utf8')

  for (const application of applications) {
    assert.match(source, new RegExp(`id: '${application}'`))
    assert.match(source, new RegExp(`href: '/${application}/'`))
  }

  assert.match(source, /<slot \/>/)
  assert.match(source, /<AccountMenu/)
})
