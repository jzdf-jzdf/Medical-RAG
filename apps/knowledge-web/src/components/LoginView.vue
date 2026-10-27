<script setup lang="ts">
import { nextTick, reactive, ref } from 'vue'
import { signIn } from '../auth'

const accountInput = ref<HTMLInputElement | null>(null)
const form = reactive({ account: '', password: '' })
const errors = reactive({ account: '', password: '' })
const showPassword = ref(false)
const submitting = ref(false)

function validate() {
  errors.account = form.account.trim() ? '' : '请输入登录账号'
  errors.password = form.password ? '' : '请输入登录密码'
  return !errors.account && !errors.password
}

function validateField(field: 'account' | 'password') {
  if (field === 'account') errors.account = form.account.trim() ? '' : '请输入登录账号'
  if (field === 'password') errors.password = form.password ? '' : '请输入登录密码'
}

async function submit() {
  if (submitting.value || !validate()) {
    await nextTick()
    if (errors.account) accountInput.value?.focus()
    return
  }
  submitting.value = true
  await new Promise((resolve) => window.setTimeout(resolve, 320))
  signIn(form.account)
}
</script>

<template>
  <main class="login-page">
    <section class="login-story" aria-labelledby="platform-name">
      <div class="brand-lockup">
        <span class="brand-mark" aria-hidden="true"><svg viewBox="0 0 26 12"><path d="M0 0h2v12H0zM12 0H6C4.9 0 4 .9 4 2v8c0 1.1.9 2 2 2h6v-2H6V2h6zM15 0h2v5h7V0h2v12h-2V7h-7v5h-2z" /></svg></span>
        <span><strong id="platform-name">循证医学平台</strong><small>ICH EVIDENCE OS</small></span>
      </div>
      <div class="story-copy">
        <span class="eyebrow">Evidence, connected.</span>
        <h1>让证据走完从<br><em>知识到决策</em>的全程。</h1>
        <p>在一个工作台中管理医学知识、开展智能问答，并持续评测回答质量。</p>
      </div>
      <ol class="product-map" aria-label="平台功能">
        <li><span>01</span><div><b>知识空间</b><small>文献、分块与索引</small></div></li>
        <li><span>02</span><div><b>智能问答</b><small>检索、推理与证据</small></div></li>
        <li><span>03</span><div><b>评测中心</b><small>题库、测试与追踪</small></div></li>
      </ol>
      <p class="story-foot">面向脑出血专病的循证知识基础设施</p>
    </section>

    <section class="login-panel" aria-labelledby="login-title">
      <div class="mobile-brand" aria-hidden="true"><span>ICH</span><b>循证医学平台</b></div>
      <form novalidate @submit.prevent="submit">
        <div class="form-heading">
          <span class="eyebrow">Welcome back</span>
          <h2 id="login-title">登录工作台</h2>
          <p>输入内部账号，继续访问循证医学服务。</p>
        </div>

        <label class="field" for="login-account">
          <span>账号</span>
          <input id="login-account" ref="accountInput" v-model.trim="form.account" name="username" type="text" autocomplete="username" placeholder="请输入账号或邮箱" :aria-invalid="Boolean(errors.account)" :aria-describedby="errors.account ? 'account-error' : undefined" @blur="validateField('account')">
          <small v-if="errors.account" id="account-error" role="alert">{{ errors.account }}</small>
        </label>

        <label class="field" for="login-password">
          <span>密码</span>
          <span class="password-control">
            <input id="login-password" v-model="form.password" name="password" :type="showPassword ? 'text' : 'password'" autocomplete="current-password" placeholder="请输入密码" :aria-invalid="Boolean(errors.password)" :aria-describedby="errors.password ? 'password-error' : undefined" @blur="validateField('password')">
            <button type="button" :aria-label="showPassword ? '隐藏密码' : '显示密码'" :aria-pressed="showPassword" @click="showPassword = !showPassword">
              <svg v-if="!showPassword" viewBox="0 0 24 24" aria-hidden="true"><path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Z" /><circle cx="12" cy="12" r="2.5" /></svg>
              <svg v-else viewBox="0 0 24 24" aria-hidden="true"><path d="M3 3l18 18M10.5 6.2A10.5 10.5 0 0 1 12 6c6 0 9.5 6 9.5 6a16 16 0 0 1-2.3 2.9M6.2 6.2C3.8 7.9 2.5 12 2.5 12s3.5 6 9.5 6a10 10 0 0 0 3-.5M9.9 9.9a3 3 0 0 0 4.2 4.2" /></svg>
            </button>
          </span>
          <small v-if="errors.password" id="password-error" role="alert">{{ errors.password }}</small>
        </label>

        <button class="submit-button" type="submit" :disabled="submitting">
          <span v-if="submitting" class="spinner" aria-hidden="true"></span>
          {{ submitting ? '正在进入工作台…' : '进入工作台' }}
          <svg v-if="!submitting" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14m-5-5 5 5-5 5" /></svg>
        </button>

        <div class="trial-note" role="note">
          <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9" /><path d="M12 10v6m0-9v.5" /></svg>
          <p><b>内部试用模式</b><span>暂未接入账号校验；填写任意账号与密码即可进入。所有账号访问同一套业务数据。</span></p>
        </div>
      </form>
      <footer>仅供研究与专业辅助，不替代临床判断</footer>
    </section>
  </main>
</template>

<style scoped>
.login-page { --login-ink: #173b43; --login-muted: #657d80; --login-line: #d4e0de; --login-primary: #14736d; width: 100%; min-height: 100dvh; padding: 0 !important; display: grid; grid-template-columns: minmax(420px, 1.08fr) minmax(440px, .92fr); color: var(--login-ink); background: #f6f8f7; }
.login-story { position: relative; min-height: 100dvh; padding: clamp(32px, 5vw, 72px); display: flex; flex-direction: column; overflow: hidden; color: #e9f2f0; background: #173b43; isolation: isolate; }
.login-story::before { content: ''; position: absolute; z-index: -1; inset: 0; background: linear-gradient(rgba(132,188,181,.09) 1px, transparent 1px), linear-gradient(90deg, rgba(132,188,181,.09) 1px, transparent 1px); background-size: 56px 56px; mask-image: linear-gradient(to bottom right, #000, transparent 78%); }
.login-story::after { content: ''; position: absolute; z-index: -1; width: min(42vw, 620px); aspect-ratio: 1; right: -20%; bottom: -34%; border: 1px solid rgba(125,190,181,.2); border-radius: 50%; box-shadow: 0 0 0 84px rgba(125,190,181,.035), 0 0 0 168px rgba(125,190,181,.025); }
.brand-lockup { display: flex; align-items: center; gap: 13px; }
.brand-mark { width: 44px; height: 44px; display: grid; place-items: center; border-radius: 11px; color: #173b43; background: #dceae6; }
.brand-mark svg { width: 27px; fill: currentColor; }
.brand-lockup strong, .brand-lockup small { display: block; }
.brand-lockup strong { font-size: 15px; letter-spacing: .05em; }
.brand-lockup small { margin-top: 4px; color: #98b1af; font: 600 8px/1.2 ui-monospace, monospace; letter-spacing: .16em; }
.story-copy { margin: auto 0; padding: 8vh 0 5vh; }
.eyebrow { display: block; margin-bottom: 17px; color: #78aaa4; font: 700 10px/1.2 ui-monospace, monospace; letter-spacing: .18em; text-transform: uppercase; }
.story-copy h1 { margin: 0; max-width: 720px; color: #eef5f3; font: 500 clamp(38px, 5vw, 68px)/1.18 "Songti SC", "STSong", Georgia, serif; letter-spacing: -.045em; }
.story-copy h1 em { color: #80bbb3; font-style: normal; }
.story-copy p { max-width: 540px; margin: 27px 0 0; color: #aec1be; font-size: 14px; line-height: 1.9; }
.product-map { margin: 0; padding: 0; display: grid; grid-template-columns: repeat(3, 1fr); list-style: none; border-top: 1px solid rgba(207,229,225,.18); }
.product-map li { min-width: 0; padding: 20px 10px 6px 0; display: flex; gap: 11px; }
.product-map li + li { padding-left: 18px; border-left: 1px solid rgba(207,229,225,.13); }
.product-map > li > span { color: #6f9995; font: 500 9px ui-monospace, monospace; }
.product-map b, .product-map small { display: block; }
.product-map b { font-size: 12px; }
.product-map small { margin-top: 5px; color: #849f9c; font-size: 10px; }
.story-foot { margin: 34px 0 0; color: #76918f; font-size: 10px; letter-spacing: .12em; }
.login-panel { min-height: 100dvh; padding: 36px clamp(32px, 6vw, 92px); display: flex; align-items: center; justify-content: center; flex-direction: column; }
.login-panel form { width: min(100%, 420px); }
.mobile-brand { display: none; }
.form-heading { margin-bottom: 38px; }
.form-heading h2 { margin: 0; color: #27494e; font: 600 34px/1.25 "Songti SC", "STSong", Georgia, serif; letter-spacing: -.03em; }
.form-heading p { margin: 13px 0 0; color: var(--login-muted); font-size: 13px; line-height: 1.7; }
.field { display: block; margin-bottom: 22px; }
.field > span:first-child { display: block; margin-bottom: 8px; color: #38595e; font-size: 12px; font-weight: 700; }
.field input { width: 100%; height: 50px; padding: 0 15px; border: 1px solid var(--login-line); border-radius: 9px; outline: none; color: var(--login-ink); background: #fff; font: inherit; font-size: 16px; transition: border-color .18s ease, box-shadow .18s ease; }
.field input::placeholder { color: #9aabab; }
.field input:hover { border-color: #a8c0bc; }
.field input:focus { border-color: var(--login-primary); box-shadow: 0 0 0 3px rgba(20,115,109,.12); }
.field input[aria-invalid="true"] { border-color: #b75b47; }
.field > small { display: block; margin-top: 7px; color: #a14434; font-size: 11px; }
.password-control { position: relative; display: block; }
.password-control input { padding-right: 52px; }
.password-control button { position: absolute; top: 3px; right: 3px; width: 44px; height: 44px; padding: 0; display: grid; place-items: center; border: 0; border-radius: 7px; color: #718789; background: transparent; box-shadow: none; cursor: pointer; }
.password-control button:hover { color: var(--login-primary); background: #edf4f2; transform: none; box-shadow: none; }
.password-control button:focus-visible { outline: 2px solid var(--login-primary); outline-offset: 1px; }
.password-control svg, .submit-button svg, .trial-note svg { width: 19px; height: 19px; fill: none; stroke: currentColor; stroke-width: 1.7; stroke-linecap: round; stroke-linejoin: round; }
.submit-button { width: 100%; min-height: 50px; margin-top: 7px; padding: 0 18px; display: flex; align-items: center; justify-content: center; gap: 10px; border: 0; border-radius: 9px; color: #fff; background: var(--login-primary); box-shadow: 0 10px 24px rgba(20,115,109,.18); font-size: 14px; font-weight: 700; cursor: pointer; transition: background .18s ease, box-shadow .18s ease; }
.submit-button:hover:not(:disabled) { background: #0f625d; box-shadow: 0 13px 28px rgba(20,115,109,.23); transform: none; }
.submit-button:focus-visible { outline: 3px solid rgba(20,115,109,.25); outline-offset: 3px; }
.submit-button:disabled { cursor: wait; opacity: .68; }
.spinner { width: 17px; height: 17px; border: 2px solid rgba(255,255,255,.35); border-top-color: #fff; border-radius: 50%; animation: login-spin .7s linear infinite; }
.trial-note { margin-top: 24px; padding: 14px 15px; display: flex; align-items: flex-start; gap: 11px; border: 1px solid #d7e4e1; border-radius: 9px; color: #577476; background: #edf4f2; }
.trial-note svg { width: 18px; flex: 0 0 18px; margin-top: 1px; color: var(--login-primary); }
.trial-note p { margin: 0; font-size: 11px; line-height: 1.65; }
.trial-note b, .trial-note span { display: block; }
.trial-note b { margin-bottom: 2px; color: #31585a; }
.login-panel footer { width: min(100%, 420px); margin-top: 34px; color: #92a2a2; font-size: 10px; text-align: center; }
@keyframes login-spin { to { transform: rotate(360deg); } }
@media (max-width: 860px) { .login-page { grid-template-columns: 1fr; } .login-story { display: none; } .login-panel { padding: 32px 22px; justify-content: space-between; } .mobile-brand { width: min(100%, 420px); margin-bottom: 48px; display: flex; align-items: center; gap: 10px; color: #315257; } .mobile-brand span { width: 38px; height: 38px; display: grid; place-items: center; border-radius: 10px; color: #fff; background: var(--login-primary); font-size: 10px; font-weight: 800; } .mobile-brand b { font-size: 14px; } .form-heading h2 { font-size: 31px; } }
@media (max-width: 420px) { .login-panel { padding-inline: 18px; } .mobile-brand { margin-bottom: 34px; } .form-heading { margin-bottom: 30px; } }
@media (prefers-reduced-motion: reduce) { *, *::before, *::after { animation-duration: .01ms !important; transition-duration: .01ms !important; } }
</style>
