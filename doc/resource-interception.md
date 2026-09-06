# Resource interception and cache

`Browser` accepts a `resource_interceptor` callable. It runs before the
resource loader or real network backend and receives the resolved URL plus a
request dictionary.

```python
from pyv8c import Browser

def intercept(url, request):
    if request.get("resource_type") in {"image", "style"}:
        return False
    return None

browser = Browser(
    real_network=True,
    resource_interceptor=intercept,
    cache=True,
)
```

- Return `None` or `True` to continue the request.
- Return `False` to skip the download and return an empty response.
- Return a `Response`, mapping, or body value to provide a custom response.

Resource types include `document`, `script`, `style`, `image`, `media`,
`iframe`, and `fetch`.

The cache is disk-only; it does not retain resource bodies in process memory.
Caching is enabled only when an explicit local directory is passed, so cached
resources survive Browser restarts:

```python
browser = Browser(cache=True, cache_dir="./.pyv8c-cache")
```

Disk entries are stored under a domain directory, for example
`./.pyv8c-cache/example.com/<hash>.json`. The file key uses URL, method,
request mode, and credentials.
Set `cache=False` on `Browser` to disable caching. Requests with `cache` set to
`no-store`, `reload`, or `no-cache` bypass the disk cache.

When a disk entry is reused, `loguru` emits a DEBUG record:

```text
resource cache hit: https://example.com/app.js
```
