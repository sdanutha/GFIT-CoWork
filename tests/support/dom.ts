import { JSDOM } from 'jsdom'

// Installs jsdom browser globals (including a working localStorage) and the
// React act environment for the duration of `run`, then restores everything.
export async function withDom(run: (dom: JSDOM) => Promise<void>): Promise<void> {
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://127.0.0.1/' })
  const patched: Record<string, unknown> = {
    window: dom.window,
    document: dom.window.document,
    HTMLElement: dom.window.HTMLElement,
    HTMLInputElement: dom.window.HTMLInputElement,
    Node: dom.window.Node,
    Event: dom.window.Event,
    MouseEvent: dom.window.MouseEvent,
    localStorage: dom.window.localStorage,
  }
  const originalDescriptors = new Map(
    Object.keys(patched).map((name) => [name, Object.getOwnPropertyDescriptor(globalThis, name)]),
  )
  const actEnvironment = globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }
  const originalActEnvironment = actEnvironment.IS_REACT_ACT_ENVIRONMENT

  for (const [name, value] of Object.entries(patched)) {
    Object.defineProperty(globalThis, name, { configurable: true, writable: true, value })
  }
  actEnvironment.IS_REACT_ACT_ENVIRONMENT = true

  try {
    await run(dom)
  } finally {
    if (originalActEnvironment === undefined) delete actEnvironment.IS_REACT_ACT_ENVIRONMENT
    else actEnvironment.IS_REACT_ACT_ENVIRONMENT = originalActEnvironment
    for (const [name, descriptor] of originalDescriptors) {
      if (descriptor === undefined) delete (globalThis as Record<string, unknown>)[name]
      else Object.defineProperty(globalThis, name, descriptor)
    }
    dom.window.close()
  }
}

export function typeInto(dom: JSDOM, input: HTMLInputElement, value: string): void {
  const setter = Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype, 'value')?.set
  setter?.call(input, value)
  input.dispatchEvent(new dom.window.Event('input', { bubbles: true }))
}
