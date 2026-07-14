"""The read-only web viewer: a JSON API and the React app it feeds.

The viewer never mutates a session. It is a window onto the JSON files, not a
second way of editing them -- ``start``/``pause``/``resume``/``stop`` remain the
CLI's job, so there is exactly one writer and no chance of the browser and the
Shortcuts racing each other.

* :mod:`web.api`    -- builds the JSON payloads. Pure; no sockets, no HTTP.
* :mod:`web.server` -- a stdlib HTTP server that serves those payloads and the
  built React app.
"""
