// SPDX-License-Identifier: Apache-2.0
const { searchDocuments, excerpt } = await import(`./search.js${new URL(import.meta.url).search}`);

const searchDialog = document.querySelector("#search-dialog");
const menuDialog = document.querySelector("#menu-dialog");
const searchInput = document.querySelector("#search-input");
const searchStatus = document.querySelector("#search-status");
const searchResults = document.querySelector("#search-results");
const searchRetry = document.querySelector("#search-retry");
const themeToggle = document.querySelector("#theme-toggle");
const toast = document.querySelector("#toast");
let searchIndex;
let searchVersion = 0;
let toastTimer;

function notify(message) {
    clearTimeout(toastTimer);
    toast.textContent = message;
    toast.hidden = false;
    toastTimer = setTimeout(() => { toast.hidden = true; }, 4500);
}

function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    themeToggle.setAttribute("aria-label", `Switch to ${theme === "dark" ? "light" : "dark"} theme`);
    document.querySelector('meta[name="theme-color"]').content = theme === "dark" ? "#0b0c10" : "#f7f6fb";
}

try {
    const saved = localStorage.getItem("muse-docs-theme");
    if (saved === "light" || saved === "dark") applyTheme(saved);
    else if (saved) console.warn("Unrecognized saved documentation theme; using the default dark theme.");
} catch (error) {
    console.warn("Saved documentation theme could not be read.", error);
}
themeToggle.addEventListener("click", () => {
    const theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    applyTheme(theme);
    try { localStorage.setItem("muse-docs-theme", theme); }
    catch (error) {
        console.warn("Documentation theme could not be saved.", error);
        notify("Theme changed, but this browser could not save your preference.");
    }
});

async function loadSearchIndex() {
    if (!searchIndex) {
        searchIndex = fetch(new URL("../search-index.json", import.meta.url)).then(async (response) => {
            if (!response.ok) throw new Error(`Search index HTTP ${response.status}`);
            const data = await response.json();
            if (!Array.isArray(data) || !data.every((item) => item &&
                ["title", "heading", "text", "url"].every((field) => typeof item[field] === "string") &&
                /^[a-z0-9-]+\.html#[a-z0-9_-]+$/.test(item.url))) {
                throw new Error("Search index has an invalid format.");
            }
            return data;
        }).catch((error) => { searchIndex = undefined; throw error; });
    }
    return searchIndex;
}

function renderResults(results, query) {
    searchResults.replaceChildren();
    for (const result of results) {
        const link = document.createElement("a");
        link.className = "search-result";
        link.href = result.url;
        const label = document.createElement("span");
        label.className = "result-section";
        label.textContent = result.title;
        const heading = document.createElement("strong");
        heading.textContent = result.heading;
        const text = document.createElement("p");
        text.textContent = excerpt(result.text, query);
        link.append(label, heading, text);
        link.addEventListener("click", () => searchDialog.close());
        searchResults.append(link);
    }
}

async function updateSearch() {
    const version = ++searchVersion;
    const query = searchInput.value.trim();
    searchRetry.hidden = true;
    if (!query) {
        renderResults([], "");
        searchStatus.textContent = "Try a feature, an error, or a command. For example: Copilot, Wi-Fi, or TLS.";
        return;
    }
    searchStatus.textContent = "Searching...";
    try {
        const documents = await loadSearchIndex();
        if (version !== searchVersion) return;
        const results = searchDocuments(documents, query);
        renderResults(results, query);
        searchStatus.textContent = results.length ? `${results.length} matching sections` :
            "No matching sections. Try a shorter term, or browse the menu.";
    } catch (error) {
        if (version !== searchVersion) return;
        console.error("Documentation search failed.", error);
        renderResults([], "");
        searchStatus.textContent = "Search could not be loaded. Retry, or use the documentation menu.";
        searchRetry.hidden = false;
    }
}

function openSearch() {
    if (menuDialog.open) menuDialog.close();
    if (!searchDialog.open) searchDialog.showModal();
    searchInput.focus();
    searchInput.select();
    void updateSearch();
}
document.querySelectorAll("[data-search]").forEach((button) => button.addEventListener("click", openSearch));
searchInput.addEventListener("input", () => { void updateSearch(); });
searchRetry.addEventListener("click", () => { void updateSearch(); });
document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
        const dialog = searchDialog.open ? searchDialog : menuDialog.open ? menuDialog : undefined;
        if (dialog) {
            event.preventDefault();
            dialog.close();
        }
    }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        openSearch();
    }
});
document.querySelector("#menu-toggle").addEventListener("click", () => menuDialog.showModal());
menuDialog.querySelectorAll("a").forEach((link) => link.addEventListener("click", () => menuDialog.close()));
document.querySelectorAll("[data-close-dialog]").forEach((button) => button.addEventListener("click", () =>
    button.closest("dialog").close()));
document.querySelectorAll("dialog").forEach((dialog) => dialog.addEventListener("click", (event) => {
    const bounds = dialog.getBoundingClientRect();
    if (event.target === dialog && (event.clientX < bounds.left || event.clientX > bounds.right ||
        event.clientY < bounds.top || event.clientY > bounds.bottom)) dialog.close();
}));

for (const pre of document.querySelectorAll(".prose pre")) {
    const code = pre.querySelector("code");
    if (!code) continue;
    const block = document.createElement("div");
    block.className = "code-block";
    const header = document.createElement("div");
    header.className = "code-header";
    const label = document.createElement("span");
    label.textContent = code.className.replace("language-", "") || "Text";
    const button = document.createElement("button");
    button.className = "copy-button";
    button.type = "button";
    button.textContent = "Copy";
    button.setAttribute("aria-label", "Copy this code block");
    button.addEventListener("click", async () => {
        if (!navigator.clipboard || !window.isSecureContext) {
            notify("Copy requires a secure browser context. Select and copy the text manually.");
            return;
        }
        try {
            await navigator.clipboard.writeText(code.textContent);
            button.textContent = "Copied";
            notify("Code copied to clipboard.");
            setTimeout(() => { button.textContent = "Copy"; }, 1800);
        } catch (error) {
            console.warn("Code could not be copied.", error);
            notify("Copy failed. Select and copy the text manually.");
        }
    });
    header.append(label, button);
    pre.replaceWith(block);
    block.append(header, pre);
    pre.tabIndex = 0;
}

for (const heading of document.querySelectorAll(".prose h2[id], .prose h3[id]")) {
    const link = document.createElement("a");
    link.className = "heading-link";
    link.href = `#${heading.id}`;
    link.setAttribute("aria-label", `Link to ${heading.textContent}`);
    link.textContent = "#";
    heading.append(link);
}

const tocLinks = [...document.querySelectorAll(".page-toc a")];
if (tocLinks.length && "IntersectionObserver" in window) {
    const observer = new IntersectionObserver((entries) => {
        for (const entry of entries) {
            if (!entry.isIntersecting) continue;
            for (const link of tocLinks) {
                if (link.hash === `#${entry.target.id}`) link.setAttribute("aria-current", "location");
                else link.removeAttribute("aria-current");
            }
        }
    }, { rootMargin: "-10% 0px -65% 0px" });
    document.querySelectorAll(".prose h2[id], .prose h3[id]").forEach((heading) => observer.observe(heading));
}
