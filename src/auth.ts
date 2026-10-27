import { readonly, ref } from 'vue'

export interface UserSession {
  account: string
  displayName: string
  signedInAt: string
}

const STORAGE_KEY = 'medical-rag-session-v1'

function readSession(): UserSession | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const value = JSON.parse(raw) as Partial<UserSession>
    if (!value.account || !value.displayName || !value.signedInAt) return null
    return value as UserSession
  } catch {
    localStorage.removeItem(STORAGE_KEY)
    return null
  }
}

const currentSession = ref<UserSession | null>(readSession())

export const session = readonly(currentSession)

export function signIn(account: string) {
  const normalizedAccount = account.trim()
  const displayName = normalizedAccount.includes('@')
    ? normalizedAccount.split('@')[0]
    : normalizedAccount
  const nextSession: UserSession = {
    account: normalizedAccount,
    displayName,
    signedInAt: new Date().toISOString(),
  }
  localStorage.setItem(STORAGE_KEY, JSON.stringify(nextSession))
  currentSession.value = nextSession
}

export function signOut() {
  localStorage.removeItem(STORAGE_KEY)
  currentSession.value = null
}
