// Use with: node --import ./tests/js/register.mjs <script>
// Maps the browser's absolute module paths ("/x.js") to files in web/ (see hooks.mjs for the rule).
import { registerHooks } from "node:module";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const WEB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../web");

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith("/") && specifier.endsWith(".js")) {
      return { url: pathToFileURL(path.join(WEB, specifier)).href, shortCircuit: true, format: "module" };
    }
    return nextResolve(specifier, context);
  },
});
