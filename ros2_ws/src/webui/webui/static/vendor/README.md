# Vendored browser libraries

## `roslib.min.js` — roslibjs 1.4.1

    https://cdn.jsdelivr.net/npm/roslib@1.4.1/build/roslib.min.js
    sha256  3c510df2bc04be9d5b07efafb01eb5f473d8a5092d93292351a715ca60daf9d6
    bytes   66315

Pinned in `versions.lock`. Served from this repo rather than from a CDN because the stack
is expected to run on a machine with no outbound network, and a page whose control library
fails to load is a page whose buttons silently do nothing.

**Why 1.4.1 and not 2.1.0** (the current `latest`, published 2026-03-03). roslibjs 2.x ships
as an ES module that dynamic-imports its transport: `dist/RosLib.js` plus
`NativeWebSocketTransport-*.js`, `WsWebSocketTransport-*.js` and an `importmap.js` — four
files to vendor and pin, with hashed names that change on every release, instead of one. It
buys this page nothing: it needs `Ros`, `Topic`, subscribe and publish, which 1.4.1 has and
which the rosbridge v2 protocol has not changed. Revisit if a future need is 2.x-only.
