// Minimal DOM stand-in for loading the frontend in Node. Every property read or call returns another stub,
// so top-level wiring (addEventListener, querySelectorAll, ...) runs without a browser. Not a DOM implementation.
export function makeStub() {
  const target = function stub() {};
  return new Proxy(target, {
    get(t, k) {
      if (k === "then" || typeof k === "symbol") return undefined;
      if (k in t) return t[k];
      t[k] = makeStub();
      return t[k];
    },
    set(t, k, v) { t[k] = v; return true; },
    apply() { return makeStub(); },
    construct() { return makeStub(); },
  });
}

// Globals a page provides that the frontend touches at load time.
export function installBrowserGlobals() {
  globalThis.window = globalThis;
  globalThis.document = makeStub();
  globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
  globalThis.fetch = () => new Promise(() => {});
  globalThis.EventSource = function EventSource() { return makeStub(); };
  globalThis.matchMedia = () => ({ matches: false, addEventListener() {} });
}
