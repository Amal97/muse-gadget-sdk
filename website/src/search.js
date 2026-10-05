// SPDX-License-Identifier: Apache-2.0
function normalize(value) {
    return value.toLocaleLowerCase("en").replace(/[^\p{L}\p{N}]+/gu, " ").trim();
}

export function searchDocuments(documents, query, limit = 8) {
    if (!Array.isArray(documents) || typeof query !== "string") throw new TypeError("Invalid search input.");
    const terms = normalize(query).split(/\s+/).filter(Boolean);
    if (!terms.length) return [];
    return documents.map((document) => {
        const title = normalize(document.title);
        const heading = normalize(document.heading);
        const text = normalize(document.text);
        const haystack = `${title} ${heading} ${text}`;
        if (!terms.every((term) => haystack.includes(term))) return null;
        const score = terms.reduce((sum, term) => sum +
            (title.includes(term) ? 12 : 0) + (heading.includes(term) ? 8 : 0) +
            Math.min(text.split(term).length - 1, 5), 0);
        return { ...document, score };
    }).filter(Boolean).sort((a, b) => b.score - a.score || a.title.localeCompare(b.title)).slice(0, limit);
}

export function excerpt(text, query, length = 170) {
    const compact = text.replace(/\s+/g, " ").trim();
    const first = query.trim().split(/\s+/)[0]?.toLowerCase();
    const match = first ? compact.toLowerCase().indexOf(first) : -1;
    const start = match > 45 ? match - 45 : 0;
    const end = start + length;
    return `${start ? "..." : ""}${compact.slice(start, end)}${end < compact.length ? "..." : ""}`;
}
