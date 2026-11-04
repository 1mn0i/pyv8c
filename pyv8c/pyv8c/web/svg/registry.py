"""SVG local-name to interface mapping."""

#: The SVG local names whose interface is not their own capitalised name.
_SVG_NAMES = {"svg": "SVGSVGElement", "tspan": "SVGTSpanElement"}


def svg_interface_name(local_name):
    """The interface Chrome gives an SVG local name, matched case for case.

    Whether the realm declares it is its question: Chrome answers ``SVGElement`` for a
    name it has none for, and so does the caller when this one is not in the baseline.
    The filter primitives keep a capital ``FE`` rather than the name's own ``fe``.
    """
    match = _SVG_NAMES.get(local_name)
    if match is not None:
        return match
    capital = local_name[:2].upper() + local_name[2:] if local_name.startswith("fe") \
        else local_name[:1].upper() + local_name[1:]
    return "SVG" + capital + "Element"

__all__ = ["svg_interface_name"]
