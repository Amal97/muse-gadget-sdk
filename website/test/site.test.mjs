// SPDX-License-Identifier: Apache-2.0
import assert from "node:assert/strict";
import { test, before, after } from "node:test";
import { mkdtemp, readFile, readdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createHash } from "node:crypto";
import { buildSite, parseDocumentation, slug } from "../build.mjs";
import { groups } from "../config.mjs";
import { searchDocuments, excerpt } from "../src/search.js";

let output, source, documentation;
before(async () => {
    output = await mkdtemp(join(tmpdir(), "muse-website-test-"));
    source = await readFile(new URL("../../README.md", import.meta.url), "utf8");
    documentation = await buildSite(output, source);
});
after(async () => {
    if (output) await rm(output, { recursive: true });
});

test("every README chapter is published and registered in navigation", async () => {
    const expected = [...source.matchAll(/^## (.+)$/gm)].map((match) => slug(match[1])).filter((id) => id !== "contents");
    assert.deepEqual(documentation.pages.map((page) => page.id), expected);
    assert.deepEqual(new Set(groups.flatMap((group) => group.pages.map(([id]) => id))), new Set(expected));
    assert.equal((await readdir(output)).filter((file) => file.endsWith(".html")).length, expected.length + 1);
    assert.equal(await readFile(join(output, "source.md"), "utf8"), source);
    const manifest = JSON.parse(await readFile(join(output, "manifest.json"), "utf8"));
    assert.equal(manifest.readmeHash, createHash("sha256").update(source).digest("hex").slice(0, 12));
});

test("every original heading and code block is preserved, not summarized away", async () => {
    for (const page of documentation.pages) {
        const html = await readFile(join(output, `${page.id}.html`), "utf8");
        assert.equal((html.match(/<h1\b/g) ?? []).length, 1, page.id);
        assert.ok(html.includes(`id="${page.id}"`), page.id);
        for (const heading of page.headings) assert.ok(html.includes(`id="${heading.id}"`), heading.id);
        for (const token of page.tokens.filter((token) => ["fence", "code_block"].includes(token.type))) {
            assert.ok(html.includes(documentation.markdown.utils.escapeHtml(token.content)), `${page.id}: missing code`);
        }
        assert.ok(html.includes('aria-current="page"'), page.id);
        assert.ok(html.includes('href="#main-content"'), page.id);
    }
});

test("local generated links and fragment destinations resolve, including subpath hosting", async () => {
    const files = (await readdir(output)).filter((file) => file.endsWith(".html"));
    const content = new Map(await Promise.all(files.map(async (file) => [file, await readFile(join(output, file), "utf8")])));
    for (const [file, html] of content) {
        for (const [, target] of html.matchAll(/href="([^"]+)"/g)) {
            if (/^[a-z][a-z0-9+.-]*:/i.test(target)) continue;
            assert.ok(!target.startsWith("/"), `Absolute path breaks GitHub Pages: ${target}`);
            const [path, fragment] = target.split("#");
            const local = path || file;
            if (local.startsWith("assets/")) continue;
            assert.ok(content.has(local), `${file}: missing ${target}`);
            if (fragment) assert.ok(content.get(local).includes(`id="${fragment}"`), `${file}: missing ${target}`);
        }
    }
});

test("Markdown link rendering is repeatable and source paths become repository links", () => {
    const parsed = parseDocumentation(source);
    const tokens = parsed.pages.find((page) => page.id === "development-and-testing").tokens;
    const first = parsed.markdown.renderer.render(tokens, parsed.markdown.options, {});
    const second = parsed.markdown.renderer.render(tokens, parsed.markdown.options, {});
    assert.equal(second, first);
    assert.match(first, /github\.com\/Amal97\/muse-gadget-sdk\/blob\/feature\/open-claw-with-muse\/esp32\//);
    assert.match(first, /github\.com\/Amal97\/muse-gadget-sdk\/tree\/feature\/open-claw-with-muse\/esp32\//);
    assert.ok(!first.includes('href="esp32/'));
});

test("new or removed README sections fail explicitly instead of disappearing", () => {
    assert.throws(() => parseDocumentation(`${source}\n## Unexpected feature\nContent.\n`), /no navigation entry/);
    assert.throws(() => parseDocumentation(source.replace("## Choose a mode", "### Choose a mode")), /missing README section/);
});

test("all subsection headings are searchable and search URLs resolve", async () => {
    const index = JSON.parse(await readFile(join(output, "search-index.json"), "utf8"));
    assert.equal(index.length, documentation.pages.reduce((count, page) => count + page.headings.length + 1, 0));
    for (const item of index) {
        const [file, fragment] = item.url.split("#");
        const html = await readFile(join(output, file), "utf8");
        assert.ok(html.includes(`id="${fragment}"`));
    }
    for (const query of ["Copilot", "TLS", "Wi-Fi", "calendar", "OpenAI", "backup", "NVS"]) {
        assert.ok(searchDocuments(index, query).length, query);
    }
    assert.ok(searchDocuments(index, "idf.py").some((result) => result.text.includes("idf.py")));
});

test("search prioritizes headings, accepts punctuation, and handles no-match input", () => {
    const docs = [
        { title: "General", heading: "Intro", text: "Copilot settings.", url: "a.html#a" },
        { title: "GitHub Copilot", heading: "Wi-Fi setup", text: "Connect your device.", url: "b.html#b" },
    ];
    assert.equal(searchDocuments(docs, "copilot")[0].url, "b.html#b");
    assert.equal(searchDocuments(docs, "WI FI")[0].url, "b.html#b");
    assert.deepEqual(searchDocuments(docs, ""), []);
    assert.deepEqual(searchDocuments(docs, "nonexistent term"), []);
    assert.throws(() => searchDocuments(docs, null), /Invalid search input/);
    assert.equal(searchDocuments(docs, "copilot", 1).length, 1);
});

test("excerpts are bounded and preserve untrusted text as text", () => {
    assert.ok(excerpt("a ".repeat(500), "a").length <= 176);
    assert.ok(excerpt("before ".repeat(50) + "Copilot is here", "copilot").startsWith("..."));
    assert.equal(excerpt("<script>alert(1)</script>", "script"), "<script>alert(1)</script>");
});

function luminance(hex) {
    if (hex.length === 4) hex = "#" + [...hex.slice(1)].map((value) => value.repeat(2)).join("");
    const colors = hex.match(/[a-f0-9]{2}/gi).map((value) => parseInt(value, 16) / 255)
        .map((value) => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4);
    return colors[0] * .2126 + colors[1] * .7152 + colors[2] * .0722;
}

test("both reading themes meet 4.5:1 contrast for text and navigation", async () => {
    const css = await readFile(new URL("../src/style.css", import.meta.url), "utf8");
    for (const selector of [":root {", ':root[data-theme="light"] {']) {
        const block = css.slice(css.indexOf(selector)).split("}")[0];
        const values = Object.fromEntries([...block.matchAll(/--([\w-]+): (#[a-f0-9]{3,6});/gi)].map((match) => [match[1], match[2]]));
        for (const text of ["text", "muted", "subtle", "accent"]) {
            for (const background of ["background", "panel", "panel-raised"]) {
                const a = luminance(values[text]), b = luminance(values[background]);
                const ratio = (Math.max(a, b) + .05) / (Math.min(a, b) + .05);
                assert.ok(ratio >= 4.5, `${selector} ${text}/${background}: ${ratio.toFixed(2)}`);
            }
        }
    }
});
