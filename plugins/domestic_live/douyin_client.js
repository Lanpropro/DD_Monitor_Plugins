// Inspect registration only. Calling an incomplete webpack module can poison its export cache.
(require) => {
    if (!require?.m) return {ready: false, reason: 'module'};
    const entry = Object.entries(require.m).find(([, factory]) => {
        const code = factory.toString();
        return code.includes('/webcast/room/chat/') && code.includes('paste_edit_method');
    });
    if (!entry) return {ready: false, reason: 'module'};
    const code = entry[1].toString();
    const parameters = code.match(/^[^(]*\(([^)]*)\)/)?.[1].split(',');
    const name = parameters?.[2]?.trim();
    if (name && /^[$A-Z_a-z][\w$]*$/.test(name)) {
        const escaped = name.replace(/\$/g, '\\$');
        const imports = new RegExp('(?:^|[^\\w$.])' + escaped + '\\(\\s*["\']?(\\d+)["\']?\\s*\\)', 'g');
        for (const match of code.matchAll(imports)) {
            if (typeof require.m[match[1]] !== 'function') return {ready: false, reason: 'dependency'};
        }
    }
    return {ready: true, reason: 'ready', id: entry[0]};
}
