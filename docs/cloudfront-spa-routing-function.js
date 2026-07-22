function handler(event) {
    var request = event.request;
    var uri = request.uri;

    // Never touch API requests — a real 404 from the backend must reach the
    // browser as a real 404, not get remapped to the SPA shell.
    if (uri.startsWith('/api/')) {
        return request;
    }

    // Real static assets (js, css, png, svg, ico, map, json, etc.) have a file
    // extension — let them hit S3 as-is.
    if (uri.includes('.')) {
        return request;
    }

    // Everything else is a client-side SPA route (e.g. /fit/123, /dashboard,
    // a deep link or a refresh on one of those routes) — no literal S3 object
    // exists at this path. Rewrite to /index.html so S3 returns the real app
    // shell directly, instead of a 404 that would otherwise need the
    // distribution-wide CustomErrorResponses remap this function replaces.
    request.uri = '/index.html';
    return request;
}
