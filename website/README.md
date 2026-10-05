# Muse Companion documentation website

A static, searchable website generated from the [project README](../README.md).
The overview and 21 topic pages preserve the full installation instructions,
commands, tables, warnings, troubleshooting, and reference information.

Features include grouped navigation, subsection links, previous/next pages,
responsive mobile menus, light/dark themes, command-copy buttons, and search
with **Ctrl+K / Cmd+K**. Pages remain readable without JavaScript.

This is a documentation website, not a live device dashboard. It does not
connect to your ESP32, OpenAI account, OpenClaw bridge, or Copilot session.
There are no analytics, external fonts, or runtime CDN dependencies.

## Build and preview

Requirements: **Node.js 22 or newer**, npm, and **Python 3** for the bundled
preview command. From the repository root:

```sh
npm --prefix website ci
npm --prefix website test
npm --prefix website run dev
```

Open **http://127.0.0.1:4173/**. Stop the preview with **Ctrl+C**.

To rebuild without starting a server:

```sh
npm --prefix website run build
```

The generated site is written to `website/dist/`, which is ignored by Git.
Changes are not watched automatically: rebuild and refresh after editing.
If port 4173 is occupied, use another port without stopping unrelated services:

```sh
python3 -m http.server 4174 --bind 127.0.0.1 --directory website/dist
```

## Editing content and design

- Edit the [root README](../README.md) for documentation changes.
- Each level-two heading becomes a page, except `Contents`.
- Register new or renamed chapters in [config.mjs](./config.mjs). Builds fail
  if a chapter is missing from navigation or a registered chapter disappears.
- Edit [style.css](./src/style.css) for design,
  [app.js](./src/app.js) for browser controls, and
  [search.js](./src/search.js) for search ranking.
- [build.mjs](./build.mjs) generates pages, rewrites README links, and creates
  the search index. Generated pages should not be edited directly.
- Update the repository and branch constants in [config.mjs](./config.mjs)
  when publishing from a different fork or branch.

The output contains an exact `source.md` copy of the root README and a
`manifest.json` with its SHA-256 provenance fingerprint. Asset cache keys
also incorporate the CSS, JavaScript, and favicon contents.

The Node tests check chapter and code-block fidelity, local links and
fragments, repository links, search behavior, navigation completeness,
repeatable rendering, and both themes' text contrast. Browser checks should
also cover narrow screens, dialogs, theme persistence, and clipboard feedback.

## Hosting

Upload **the contents of `website/dist/`** to any static HTTPS host. No server
application, database, API key, or device connection is required. Serve it
over HTTP/HTTPS rather than opening HTML directly as `file://`, because
JavaScript modules and the search index require a web server.

All internal links and assets are relative, so the site works at a domain
root or under a repository subpath such as `/muse-gadget-sdk/`. Keep the
generated directory structure intact and use `index.html` as the entry page.
HTTPS is recommended; clipboard access depends on browser permissions and
a secure context. Manual selection/copying remains available.

### GitHub Pages

This change does not enable or publish GitHub Pages automatically. To
publish manually:

1. Run the tests and build commands above.
2. Copy the generated output into a dedicated publishing branch in your
   repository, with `index.html` at its root. Include the generated
   `.nojekyll` file and all assets, topic pages, and search data.
3. In **Settings -> Pages**, select **Deploy from a branch**, your publishing
   branch, and **/ (root)**.
4. GitHub provides the final site URL after its Pages deployment finishes.

Alternatively, configure your host's build pipeline to run
`npm --prefix website ci`, `npm --prefix website test`, and
`npm --prefix website run build`, publishing only `website/dist/`.
Use a separate publishing branch or an isolated CI checkout; do not overwrite
your source branch with generated output.

Rebuild and republish when the root README or website source changes.
The site ships only public documentation: never put real API keys, bridge
tokens, private certificates, or personal data in the README or static assets.
