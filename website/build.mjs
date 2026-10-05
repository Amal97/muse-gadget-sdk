// SPDX-License-Identifier: Apache-2.0
import { mkdir, readFile, writeFile, copyFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { resolve, dirname } from "node:path";
import { createHash } from "node:crypto";
import { statSync } from "node:fs";
import MarkdownIt from "markdown-it";
import { groups, repository, branch } from "./config.mjs";

const directory = dirname(fileURLToPath(import.meta.url));
const root = resolve(directory, "..");
export const escape = (value) => String(value).replace(/[&<>"']/g,
    (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]);
export const slug = (value) => value.toLowerCase().replace(/[^\p{L}\p{N}_\s-]/gu, "").replace(/ /g, "-");
const icons = {
    search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4.5 4.5"/>',
    menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
    theme: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/>',
    arrow: '<path d="M5 12h14m-6-6 6 6-6 6"/>',
};
const icon = (name) => `<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6">${icons[name]}</svg>`;

function tokenText(tokens) {
    return tokens.map((token) => token.children ? tokenText(token.children) :
        ["text", "code_inline", "fence", "code_block"].includes(token.type) ? token.content : "").join(" ");
}

export function parseDocumentation(source) {
    const markdown = new MarkdownIt({ html: true, linkify: false, typographer: false });
    const tokens = markdown.parse(source, {});
    const counts = new Map();
    const anchors = new Map();
    const starts = [];
    for (let index = 0; index < tokens.length; index++) {
        const token = tokens[index];
        if (token.type !== "heading_open") continue;
        const title = tokenText(tokens[index + 1].children ?? [tokens[index + 1]]).trim();
        const base = slug(title);
        const count = counts.get(base) ?? 0;
        counts.set(base, count + 1);
        const id = count ? `${base}-${count}` : base;
        token.attrSet("id", id);
        if (token.tag === "h2") starts.push({ index, id, title, line: token.map[0] });
    }
    const lines = source.split("\n");
    const pages = starts.filter((start) => start.id !== "contents").map((start) => {
        const next = starts.find((other) => other.index > start.index);
        const pageTokens = tokens.slice(start.index, next?.index ?? tokens.length);
        const headings = [];
        for (let index = 0; index < pageTokens.length; index++) {
            const token = pageTokens[index];
            if (token.type === "heading_open") {
                const id = token.attrGet("id");
                anchors.set(id, `${start.id}.html#${id}`);
                if (index) headings.push({ id, title: tokenText(pageTokens[index + 1].children ?? []).trim() });
            }
        }
        return { id: start.id, title: start.title, tokens: pageTokens, headings,
            source: lines.slice(start.line, next?.line ?? lines.length).join("\n") };
    });
    const firstSection = starts[0]?.index ?? tokens.length;
    const intro = tokens.slice(0, firstSection).filter((token, index, all) =>
        token.type !== "html_block" && !(token.type.startsWith("heading_") && token.tag === "h1") &&
        !(token.type === "inline" && all[index - 1]?.type === "heading_open"));
    const configured = new Map(groups.flatMap((group) => group.pages.map(([id, label, description]) =>
        [id, { label, description, group: group.title }])));
    for (const page of pages) {
        const config = configured.get(page.id);
        if (!config) throw new Error(`README section has no navigation entry: ${page.title}`);
        Object.assign(page, config);
    }
    for (const id of configured.keys()) {
        if (!pages.some((page) => page.id === id)) throw new Error(`Navigation points to a missing README section: ${id}`);
    }
    const headingOpen = markdown.renderer.rules.heading_open ??
        ((tokens, index, options, env, renderer) => renderer.renderToken(tokens, index, options));
    markdown.renderer.rules.heading_open = (tokens, index, options, env, renderer) => {
        const tag = tokens[index].tag;
        tokens[index].tag = `h${Math.max(1, Number(tag.slice(1)) - 1)}`;
        const rendered = headingOpen(tokens, index, options, env, renderer);
        tokens[index].tag = tag;
        return rendered;
    };
    markdown.renderer.rules.heading_close = (tokens, index) =>
        `</h${Math.max(1, Number(tokens[index].tag.slice(1)) - 1)}>\n`;
    const linkOpen = markdown.renderer.rules.link_open ??
        ((tokens, index, options, env, renderer) => renderer.renderToken(tokens, index, options));
    markdown.renderer.rules.link_open = (tokens, index, options, env, renderer) => {
        const token = tokens[index];
        const href = token.attrGet("href");
        if (href?.startsWith("#") && anchors.has(href.slice(1))) token.attrSet("href", anchors.get(href.slice(1)));
        else if (href && !/^(?:[a-z][a-z0-9+.-]*:|\/\/)/i.test(href) && !href.startsWith("#")) {
            const [path, fragment] = href.split("#");
            if (path === "README.md") token.attrSet("href", fragment && anchors.has(fragment) ?
                anchors.get(fragment) : "index.html");
            else {
                const kind = statSync(resolve(root, decodeURI(path))).isDirectory() ? "tree" : "blob";
                token.attrSet("href", `${repository}/${kind}/${branch}/${path}${fragment ? `#${fragment}` : ""}`);
            }
        }
        const rendered = linkOpen(tokens, index, options, env, renderer);
        if (href !== null) token.attrSet("href", href);
        return rendered;
    };
    markdown.renderer.rules.table_open = () =>
        '<div class="table-scroll" tabindex="0" role="region" aria-label="Scrollable reference table"><table>';
    markdown.renderer.rules.table_close = () => "</table></div>\n";
    return { markdown, pages, intro, anchors };
}

function navigation(pages, current, mobile = false) {
    return `<a class="overview-link${current === "index" ? " active" : ""}" href="index.html"${current === "index" ? ' aria-current="page"' : ""}>Overview</a>
        ${groups.map((group) => `<div class="nav-group"><h2>${escape(group.title)}</h2>${group.pages.map(([id]) => {
            const page = pages.find((page) => page.id === id);
            return `<a href="${id}.html"${current === id ? ' class="active" aria-current="page"' : ""}>${escape(page.label)}</a>`;
        }).join("")}</div>`).join("")}
        ${mobile ? "" : `<div class="sidebar-note"><span class="status-dot"></span> Community-built. USB-flashed.<br>Start small. Opt in deliberately.</div>`}`;
}

function searchDialog() {
    return `<dialog id="search-dialog" aria-labelledby="search-title"><div class="dialog-top">
        <h2 id="search-title">Find your next step</h2><button class="quiet-button" type="button" data-close-dialog>Close <kbd>Esc</kbd></button></div>
        <label class="search-field">${icon("search")}<span class="sr-only">Search documentation</span>
        <input id="search-input" type="search" placeholder="Try Wi-Fi, Copilot, calendars..." autocomplete="off" maxlength="120"></label>
        <p class="search-status" id="search-status" role="status">Search setup steps, features, and commands.</p>
        <button id="search-retry" class="quiet-button" type="button" hidden>Retry search</button>
        <div id="search-results"></div><div class="search-footer">All information comes from the project README. <span><kbd>Tab</kbd> to navigate</span></div>
        </dialog>`;
}

function layout({ pages, current, title, description, content, toc = [], hash }) {
    const page = pages.find((page) => page.id === current);
    const tocHtml = toc.length ? `<aside class="page-toc" aria-label="On this page"><details open><summary>On this page</summary>
        <nav>${toc.map((heading) => `<a href="#${heading.id}">${escape(heading.title)}</a>`).join("")}</nav></details></aside>` : "";
    return `<!doctype html>
<html lang="en" data-theme="dark"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="${escape(description)}"><meta name="theme-color" content="#0b0c10">
<meta property="og:title" content="${escape(title)} | Muse Companion"><meta property="og:description" content="${escape(description)}">
<title>${escape(title)} | Muse Companion</title><link rel="icon" type="image/svg+xml" href="assets/favicon.svg">
<link rel="stylesheet" href="assets/style.css?v=${hash}"><script type="module" src="assets/app.js?v=${hash}"></script></head>
<body><a class="skip-link" href="#main-content">Skip to content</a>
<header class="site-header"><a class="brand" href="index.html" aria-label="Muse Companion documentation home"><span class="brand-mark">m</span><span>Muse<span class="brand-secondary"> / companion</span></span><span class="docs-badge">DOCS</span></a>
<div class="header-actions"><button class="search-trigger" data-search type="button" aria-label="Search documentation">${icon("search")}<span>Search docs</span><kbd>Ctrl K</kbd></button>
<button class="icon-button" id="theme-toggle" type="button" aria-label="Switch to light theme">${icon("theme")}</button>
<a class="github-link" href="${repository}/tree/${branch}">GitHub ${icon("arrow")}</a>
<button class="icon-button mobile-menu-button" id="menu-toggle" type="button" aria-label="Open documentation menu">${icon("menu")}</button></div></header>
<div class="site-layout"><aside class="sidebar" aria-label="Documentation"><nav>${navigation(pages, current)}</nav></aside>
<main id="main-content" tabindex="-1" class="${current === "index" ? "landing" : "document-main"}">
${page ? `<div class="breadcrumb"><a href="index.html">Docs</a><span>/</span><span>${escape(page.group)}</span></div>` : ""}
${content}<footer class="site-footer"><span>Built from the README. No missing fine print.</span><a href="${repository}/blob/${branch}/README.md">Read the source ${icon("arrow")}</a></footer></main>${tocHtml}</div>
<dialog id="menu-dialog" aria-labelledby="menu-title"><div class="dialog-top"><h2 id="menu-title">Documentation</h2><button class="quiet-button" type="button" data-close-dialog>Close</button></div><nav>${navigation(pages, current, true)}</nav></dialog>
${searchDialog()}<div class="toast" id="toast" role="status" aria-live="polite" hidden></div>
<noscript><p class="no-script">JavaScript is needed only for search, copy buttons, and theme controls. All guides and links remain readable.</p></noscript>
</body></html>`;
}

function deviceIllustration() {
    return `<div class="device-scene" role="img" aria-label="Illustrative round AI gadget with a clock, daily briefing, and Copilot prompt. Not a live connection.">
        <div class="orbit orbit-one"></div><div class="orbit orbit-two"></div><span class="scene-label">YOUR DAY, AT A GLANCE</span>
        <div class="device"><div class="device-screen"><span class="device-date">MONDAY / YOUR HOME</span><span class="device-time">08:00</span>
        <div class="device-divider"></div><div class="device-weather"><span class="weather-icon">+</span><span>Weather &amp; reminders<br><small>A little less context switching.</small></span></div>
        <div class="device-briefing"><span class="tiny-label">DAILY BRIEFING</span><span>Good morning.<br>Let's make room for what matters.</span></div>
        <div class="device-dots"><i></i><i></i><i></i></div></div></div>
        <div class="floating-request"><span class="status-dot"></span><div><strong>Copilot needs you</strong><span>Review. Answer. Keep moving.</span></div><span class="request-symbol">?</span></div>
        <span class="scene-caption">Illustrative UI &middot; not a live device connection</span></div>`;
}

function homeContent(documentation) {
    const { markdown, pages, intro } = documentation;
    return `<section class="hero"><div class="hero-copy"><div class="eyebrow"><span class="status-dot"></span> OPEN SOURCE / ESP32 / PERSONAL AI</div>
        <h1>A little companion.<br><span>A lot less friction.</span></h1>
        <p>Talk to your AI. Make space for your day.<br>Keep an eye on your coding work, away from your laptop.</p>
        <div class="hero-actions"><a class="button primary" href="choose-a-mode.html">Find your setup ${icon("arrow")}</a><a class="button secondary" href="what-the-project-does.html">Explore the features</a></div>
        <div class="hero-facts"><span>No Muse account for AI modes</span><span>Mac-first companion</span><span>Explicit opt-ins</span></div></div>${deviceIllustration()}</section>
        <section class="start-section"><div class="section-heading"><div><span class="eyebrow">START WITH WHAT YOU NEED</span><h2>Three ways to make it yours.</h2></div><a class="text-link" href="choose-a-mode.html">Compare every mode ${icon("arrow")}</a></div>
        <div class="path-grid">
        <a class="path-card" href="install-standalone-openai-firmware.html"><span class="card-number">01 / VOICE</span><h3>Just you and your AI.</h3><p>Start with standalone OpenAI. Speak, listen, and leave the computer unplugged after setup.</p><span class="card-footer">Standalone setup ${icon("arrow")}</span></a>
        <a class="path-card featured" href="add-the-openclaw-companion.html"><span class="card-number">02 / YOUR DAY</span><h3>Give it a home on your Mac.</h3><p>Add OpenClaw, memories, routines, reminders, weather, and a daily information screen.</p><span class="card-footer">Connect your companion ${icon("arrow")}</span></a>
        <a class="path-card" href="set-up-github-copilot.html"><span class="card-number">03 / YOUR CODE</span><h3>Keep work moving.</h3><p>Supervise a dedicated Copilot SDK session. Answer questions and review permissions from the gadget.</p><span class="card-footer">Copilot setup ${icon("arrow")}</span></a></div></section>
        <section class="overview-section"><div><span class="eyebrow">THE PROJECT, IN PLAIN LANGUAGE</span><h2>Small hardware.<br>Clear boundaries.</h2><a class="text-link" href="how-it-works.html">See how it connects ${icon("arrow")}</a></div>
        <div class="prose intro-content">${markdown.renderer.render(intro, markdown.options, {})}</div></section>
        <section class="browse-section"><div class="section-heading"><div><span class="eyebrow">THE WHOLE GUIDE, WITHOUT THE WALL OF TEXT</span><h2>Find the part you need.</h2></div></div>
        ${groups.map((group) => `<div class="topic-group"><h3>${escape(group.title)}</h3><div class="topic-grid">${group.pages.map(([id]) => {
            const page = pages.find((page) => page.id === id);
            return `<a class="topic-card" href="${id}.html"><strong>${escape(page.label)} ${icon("arrow")}</strong><span>${escape(page.description)}</span></a>`;
        }).join("")}</div></div>`).join("")}</section>`;
}

function searchEntries(documentation) {
    return documentation.pages.flatMap((page) => {
        const starts = [];
        page.tokens.forEach((token, index) => {
            if (token.type === "heading_open") starts.push(index);
        });
        return starts.map((start, index) => ({
            title: page.label,
            heading: tokenText(page.tokens[start + 1].children ?? []).trim(),
            url: `${page.id}.html#${page.tokens[start].attrGet("id")}`,
            text: tokenText(page.tokens.slice(start + 2, starts[index + 1] ?? page.tokens.length)).replace(/\s+/g, " ").trim(),
        }));
    });
}

export async function buildSite(output = resolve(directory, "dist"), source) {
    if (source === undefined) source = await readFile(resolve(root, "README.md"), "utf8");
    const documentation = parseDocumentation(source);
    const { pages, markdown } = documentation;
    const readmeHash = createHash("sha256").update(source).digest("hex").slice(0, 12);
    const fingerprint = createHash("sha256").update(source);
    await mkdir(resolve(output, "assets"), { recursive: true });
    for (const file of ["style.css", "app.js", "search.js", "favicon.svg"]) {
        fingerprint.update(await readFile(resolve(directory, "src", file)));
        await copyFile(resolve(directory, "src", file), resolve(output, "assets", file));
    }
    const hash = fingerprint.digest("hex").slice(0, 12);
    await writeFile(resolve(output, "index.html"), layout({ pages, current: "index", title: "Your personal AI companion",
        description: "A readable guide to the ESP32 personal AI companion: OpenAI voice, OpenClaw routines, and GitHub Copilot supervision.",
        content: homeContent(documentation), hash }));
    for (const [index, page] of pages.entries()) {
        const previous = index ? pages[index - 1] : null;
        const next = pages[index + 1];
        const pager = `<nav class="page-pager" aria-label="Guide navigation">${previous ?
            `<a href="${previous.id}.html"><span>Previous</span><strong>${escape(previous.label)}</strong></a>` :
            '<a href="index.html"><span>Previous</span><strong>Overview</strong></a>'}${next ?
            `<a href="${next.id}.html"><span>Next</span><strong>${escape(next.label)} ${icon("arrow")}</strong></a>` :
            '<a href="index.html"><span>Next</span><strong>Back to overview</strong></a>'}</nav>`;
        await writeFile(resolve(output, `${page.id}.html`), layout({ pages, current: page.id, title: page.label,
            description: page.description, content: `<article class="prose">${markdown.renderer.render(page.tokens, markdown.options, {})}</article>${pager}`,
            toc: page.headings, hash }));
    }
    await writeFile(resolve(output, "search-index.json"), JSON.stringify(searchEntries(documentation)));
    await writeFile(resolve(output, "source.md"), source);
    await writeFile(resolve(output, "manifest.json"), JSON.stringify({ readmeHash, assetHash: hash, pages: pages.map((page) =>
        ({ id: page.id, title: page.title, headings: page.headings })) }, null, 2));
    await writeFile(resolve(output, ".nojekyll"), "");
    return documentation;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
    const documentation = await buildSite();
    console.log(`Built overview + ${documentation.pages.length} complete README chapters in website/dist.`);
}
