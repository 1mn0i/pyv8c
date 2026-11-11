"""Schemeful-site helpers backed by the bundled Public Suffix List snapshot."""

from functools import lru_cache
import ipaddress

from .urls import canonical_host

# Never fetch at runtime: installed distributions carry their own PSL snapshot.
@lru_cache(maxsize=1)
def _extractor():
    import tldextract
    return tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None,
                                 include_psl_private_domains=True)


@lru_cache(maxsize=4096)
def site_domain(host):
    host = str(host).lower().rstrip(".")
    try:
        host = canonical_host(host)
    except UnicodeError:
        return ''
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        pass
    result = _extractor()(host)
    if result.suffix:
        return f"{result.domain}.{result.suffix}" if result.domain else result.suffix
    labels = host.split(".")
    return ".".join(labels[-2:]) if len(labels) > 1 else host


@lru_cache(maxsize=4096)
def is_public_suffix(host):
    host = str(host).lower().rstrip(".")
    try:
        host = canonical_host(host)
    except UnicodeError:
        return True
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        pass
    result = _extractor()(host)
    # The PSL prevailing rule is "*" for a suffix it does not list.
    return bool(result.suffix and not result.domain) or (not result.suffix and "." not in host)
