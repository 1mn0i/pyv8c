"""CSSOM: inline ``HTMLElement.style`` declarations and the table of property
names a ``CSSStyleDeclaration`` resolves as named properties."""
import re
from urllib.parse import urljoin

import pyv8
from pyv8 import AttributeSpec, IdlSpec, MethodSpec
from pyv8.flag import V8Attribute

from ...engine.features import interface
from ...foundation.arguments import MISSING, boolean, dom_string, integer, missing_error
from .mixins import LegacyNamedPropertyMixin, ReadOnlyIndexedPropertyMixin
from .utils import OBJECT_PROTOTYPE_NAMES, camel_name as _camel_name, dash_name as _dash_name

_NAME_RE = re.compile(r"^(?:--[A-Za-z0-9_-]+|-?[A-Za-z_][A-Za-z0-9_-]*)$")
_OBJECT_NAMES = OBJECT_PROTOTYPE_NAMES | {
    "cssText", "length", "parentRule", "cssFloat", "getPropertyPriority",
    "getPropertyValue", "item", "removeProperty", "setProperty",
}

def _property_name(value):
    value = str(dom_string(value)).strip()
    if value.startswith("--"):
        return value if _NAME_RE.match(value) else ""
    value = value.lower()
    return value if _NAME_RE.match(value) else ""

def _normalize_value(value):
    value = str(dom_string(value)).strip()
    if not value or any(ch in value for ch in "{};\x00"): return ""
    return re.sub(r"[ \t\r\n]+", " ", value)

def _split_priority(value):
    match = re.search(r"\s*!\s*important\s*$", value, re.I)
    return (value[:match.start()].rstrip(), "important") if match else (value, "")


# ``'color' in style`` only answers for properties the engine supports, so the
# list matches what Chrome reports: ``-webkit-`` forms because Blink ships them,
# no ``-moz-``/``-ms-``/``-o-``, and custom properties always accepted.

#: camelCase, as reported by a real Chrome CSSStyleDeclaration.
_CAMEL_NAMES = (
    "accentColor", "additiveSymbols", "alignContent", "alignItems", "alignSelf", "alignmentBaseline", "all",
    "anchorName", "animation", "animationComposition", "animationDelay", "animationDirection", "animationDuration",
    "animationFillMode", "animationIterationCount", "animationName", "animationPlayState", "animationRange",
    "animationRangeEnd", "animationRangeStart", "animationTimeline", "animationTimingFunction", "appRegion",
    "appearance", "ascentOverride", "aspectRatio", "backdropFilter", "backfaceVisibility", "background",
    "backgroundAttachment", "backgroundBlendMode", "backgroundClip", "backgroundColor", "backgroundImage",
    "backgroundOrigin", "backgroundPosition", "backgroundPositionX", "backgroundPositionY", "backgroundRepeat",
    "backgroundSize", "basePalette", "baselineShift", "baselineSource", "blockSize", "border", "borderBlock",
    "borderBlockColor", "borderBlockEnd", "borderBlockEndColor", "borderBlockEndStyle", "borderBlockEndWidth",
    "borderBlockStart", "borderBlockStartColor", "borderBlockStartStyle", "borderBlockStartWidth", "borderBlockStyle",
    "borderBlockWidth", "borderBottom", "borderBottomColor", "borderBottomLeftRadius", "borderBottomRightRadius",
    "borderBottomStyle", "borderBottomWidth", "borderCollapse", "borderColor", "borderEndEndRadius",
    "borderEndStartRadius", "borderImage", "borderImageOutset", "borderImageRepeat", "borderImageSlice",
    "borderImageSource", "borderImageWidth", "borderInline", "borderInlineColor", "borderInlineEnd",
    "borderInlineEndColor", "borderInlineEndStyle", "borderInlineEndWidth", "borderInlineStart",
    "borderInlineStartColor", "borderInlineStartStyle", "borderInlineStartWidth", "borderInlineStyle",
    "borderInlineWidth", "borderLeft", "borderLeftColor", "borderLeftStyle", "borderLeftWidth", "borderRadius",
    "borderRight", "borderRightColor", "borderRightStyle", "borderRightWidth", "borderSpacing", "borderStartEndRadius",
    "borderStartStartRadius", "borderStyle", "borderTop", "borderTopColor", "borderTopLeftRadius",
    "borderTopRightRadius", "borderTopStyle", "borderTopWidth", "borderWidth", "bottom", "boxShadow", "boxSizing",
    "breakAfter", "breakBefore", "breakInside", "bufferedRendering", "captionSide", "caretColor", "clear", "clip",
    "clipPath", "clipRule", "color", "colorInterpolation", "colorInterpolationFilters", "colorRendering",
    "colorScheme", "columnCount", "columnFill", "columnGap", "columnRule", "columnRuleColor", "columnRuleStyle",
    "columnRuleWidth", "columnSpan", "columnWidth", "columns", "contain", "containIntrinsicBlockSize",
    "containIntrinsicHeight", "containIntrinsicInlineSize", "containIntrinsicSize", "containIntrinsicWidth",
    "container", "containerName", "containerType", "content", "contentVisibility", "counterIncrement",
    "counterReset", "counterSet", "cursor", "cx", "cy", "d", "descentOverride", "direction", "display",
    "dominantBaseline", "emptyCells", "fallback", "fieldSizing", "fill", "fillOpacity", "fillRule", "filter", "flex",
    "flexBasis", "flexDirection", "flexFlow", "flexGrow", "flexShrink", "flexWrap", "float", "floodColor",
    "floodOpacity", "font", "fontDisplay", "fontFamily", "fontFeatureSettings", "fontKerning", "fontOpticalSizing",
    "fontPalette", "fontSize", "fontSizeAdjust", "fontStretch", "fontStyle", "fontSynthesis",
    "fontSynthesisSmallCaps", "fontSynthesisStyle", "fontSynthesisWeight", "fontVariant", "fontVariantAlternates",
    "fontVariantCaps", "fontVariantEastAsian", "fontVariantLigatures", "fontVariantNumeric", "fontVariantPosition",
    "fontVariationSettings", "fontWeight", "forcedColorAdjust", "gap", "grid", "gridArea", "gridAutoColumns",
    "gridAutoFlow", "gridAutoRows", "gridColumn", "gridColumnEnd", "gridColumnGap", "gridColumnStart", "gridGap",
    "gridRow", "gridRowEnd", "gridRowGap", "gridRowStart", "gridTemplate", "gridTemplateAreas", "gridTemplateColumns",
    "gridTemplateRows", "height", "hyphenateCharacter", "hyphenateLimitChars", "hyphens", "imageOrientation",
    "imageRendering", "inherits", "initialLetter", "initialValue", "inlineSize", "inset", "insetArea", "insetBlock",
    "insetBlockEnd", "insetBlockStart", "insetInline", "insetInlineEnd", "insetInlineStart", "isolation",
    "justifyContent", "justifyItems", "justifySelf", "left", "letterSpacing", "lightingColor", "lineBreak",
    "lineGapOverride", "lineHeight", "listStyle", "listStyleImage", "listStylePosition", "listStyleType", "margin",
    "marginBlock", "marginBlockEnd", "marginBlockStart", "marginBottom", "marginInline", "marginInlineEnd",
    "marginInlineStart", "marginLeft", "marginRight", "marginTop", "marker", "markerEnd", "markerMid", "markerStart",
    "mask", "maskClip", "maskComposite", "maskImage", "maskMode", "maskOrigin", "maskPosition", "maskRepeat",
    "maskSize", "maskType", "mathDepth", "mathShift", "mathStyle", "maxBlockSize", "maxHeight", "maxInlineSize",
    "maxWidth", "minBlockSize", "minHeight", "minInlineSize", "minWidth", "mixBlendMode", "navigation", "negative",
    "objectFit", "objectPosition", "objectViewBox", "offset", "offsetAnchor", "offsetDistance", "offsetPath",
    "offsetPosition", "offsetRotate", "opacity", "order", "orphans", "outline", "outlineColor", "outlineOffset",
    "outlineStyle", "outlineWidth", "overflow", "overflowAnchor", "overflowClipMargin", "overflowWrap", "overflowX",
    "overflowY", "overlay", "overrideColors", "overscrollBehavior", "overscrollBehaviorBlock",
    "overscrollBehaviorInline", "overscrollBehaviorX", "overscrollBehaviorY", "pad", "padding", "paddingBlock",
    "paddingBlockEnd", "paddingBlockStart", "paddingBottom", "paddingInline", "paddingInlineEnd", "paddingInlineStart",
    "paddingLeft", "paddingRight", "paddingTop", "page", "pageBreakAfter", "pageBreakBefore", "pageBreakInside",
    "pageOrientation", "paintOrder", "perspective", "perspectiveOrigin", "placeContent", "placeItems", "placeSelf",
    "pointerEvents", "position", "positionAnchor", "positionTry", "positionTryOptions", "positionTryOrder",
    "positionVisibility", "prefix", "quotes", "r", "range", "resize", "right", "rotate", "rowGap", "rubyPosition",
    "rx", "ry", "scale", "scrollBehavior", "scrollMargin", "scrollMarginBlock", "scrollMarginBlockEnd",
    "scrollMarginBlockStart", "scrollMarginBottom", "scrollMarginInline", "scrollMarginInlineEnd",
    "scrollMarginInlineStart", "scrollMarginLeft", "scrollMarginRight", "scrollMarginTop", "scrollPadding",
    "scrollPaddingBlock", "scrollPaddingBlockEnd", "scrollPaddingBlockStart", "scrollPaddingBottom",
    "scrollPaddingInline", "scrollPaddingInlineEnd", "scrollPaddingInlineStart", "scrollPaddingLeft",
    "scrollPaddingRight", "scrollPaddingTop", "scrollSnapAlign", "scrollSnapStop", "scrollSnapType", "scrollTimeline",
    "scrollTimelineAxis", "scrollTimelineName", "scrollbarColor", "scrollbarGutter", "scrollbarWidth",
    "shapeImageThreshold", "shapeMargin", "shapeOutside", "shapeRendering", "size", "sizeAdjust", "speak", "speakAs",
    "src", "stopColor", "stopOpacity", "stroke", "strokeDasharray", "strokeDashoffset", "strokeLinecap",
    "strokeLinejoin", "strokeMiterlimit", "strokeOpacity", "strokeWidth", "suffix", "symbols", "syntax", "system",
    "tabSize", "tableLayout", "textAlign", "textAlignLast", "textAnchor", "textCombineUpright", "textDecoration",
    "textDecorationColor", "textDecorationLine", "textDecorationSkipInk", "textDecorationStyle",
    "textDecorationThickness", "textEmphasis", "textEmphasisColor", "textEmphasisPosition", "textEmphasisStyle",
    "textIndent", "textOrientation", "textOverflow", "textRendering", "textShadow", "textSizeAdjust",
    "textSpacingTrim", "textTransform", "textUnderlineOffset", "textUnderlinePosition", "textWrap", "timelineScope",
    "top", "touchAction", "transform", "transformBox", "transformOrigin", "transformStyle", "transition",
    "transitionBehavior", "transitionDelay", "transitionDuration", "transitionProperty", "transitionTimingFunction",
    "translate", "types", "unicodeBidi", "unicodeRange", "userSelect", "vectorEffect", "verticalAlign", "viewTimeline",
    "viewTimelineAxis", "viewTimelineInset", "viewTimelineName", "viewTransitionClass", "viewTransitionName",
    "visibility", "webkitAlignContent", "webkitAlignItems", "webkitAlignSelf", "webkitAnimation",
    "webkitAnimationDelay", "webkitAnimationDirection", "webkitAnimationDuration",
    "webkitAnimationFillMode", "webkitAnimationIterationCount",
    "webkitAnimationName", "webkitAnimationPlayState", "webkitAnimationTimingFunction", "webkitAppRegion",
    "webkitAppearance", "webkitBackfaceVisibility", "webkitBackgroundClip", "webkitBackgroundOrigin",
    "webkitBackgroundSize", "webkitBorderAfter", "webkitBorderAfterColor", "webkitBorderAfterStyle",
    "webkitBorderAfterWidth", "webkitBorderBefore", "webkitBorderBeforeColor", "webkitBorderBeforeStyle",
    "webkitBorderBeforeWidth", "webkitBorderBottomLeftRadius", "webkitBorderBottomRightRadius", "webkitBorderEnd",
    "webkitBorderEndColor", "webkitBorderEndStyle", "webkitBorderEndWidth", "webkitBorderHorizontalSpacing",
    "webkitBorderImage", "webkitBorderRadius", "webkitBorderStart", "webkitBorderStartColor", "webkitBorderStartStyle",
    "webkitBorderStartWidth", "webkitBorderTopLeftRadius", "webkitBorderTopRightRadius", "webkitBorderVerticalSpacing",
    "webkitBoxAlign", "webkitBoxDecorationBreak", "webkitBoxDirection", "webkitBoxFlex", "webkitBoxOrdinalGroup",
    "webkitBoxOrient", "webkitBoxPack", "webkitBoxReflect", "webkitBoxShadow", "webkitBoxSizing", "webkitClipPath",
    "webkitColumnBreakAfter", "webkitColumnBreakBefore", "webkitColumnBreakInside", "webkitColumnCount",
    "webkitColumnGap", "webkitColumnRule", "webkitColumnRuleColor", "webkitColumnRuleStyle", "webkitColumnRuleWidth",
    "webkitColumnSpan", "webkitColumnWidth", "webkitColumns", "webkitFilter", "webkitFlex", "webkitFlexBasis",
    "webkitFlexDirection", "webkitFlexFlow", "webkitFlexGrow", "webkitFlexShrink", "webkitFlexWrap",
    "webkitFontFeatureSettings", "webkitFontSmoothing", "webkitHyphenateCharacter", "webkitJustifyContent",
    "webkitLineBreak", "webkitLineClamp", "webkitLocale", "webkitLogicalHeight", "webkitLogicalWidth",
    "webkitMarginAfter", "webkitMarginBefore", "webkitMarginEnd", "webkitMarginStart", "webkitMask",
    "webkitMaskBoxImage", "webkitMaskBoxImageOutset", "webkitMaskBoxImageRepeat", "webkitMaskBoxImageSlice",
    "webkitMaskBoxImageSource", "webkitMaskBoxImageWidth", "webkitMaskClip", "webkitMaskComposite", "webkitMaskImage",
    "webkitMaskOrigin", "webkitMaskPosition", "webkitMaskPositionX", "webkitMaskPositionY", "webkitMaskRepeat",
    "webkitMaskSize", "webkitMaxLogicalHeight", "webkitMaxLogicalWidth", "webkitMinLogicalHeight",
    "webkitMinLogicalWidth", "webkitOpacity", "webkitOrder", "webkitPaddingAfter", "webkitPaddingBefore",
    "webkitPaddingEnd", "webkitPaddingStart", "webkitPerspective", "webkitPerspectiveOrigin",
    "webkitPerspectiveOriginX", "webkitPerspectiveOriginY", "webkitPrintColorAdjust", "webkitRtlOrdering",
    "webkitRubyPosition", "webkitShapeImageThreshold", "webkitShapeMargin", "webkitShapeOutside",
    "webkitTapHighlightColor", "webkitTextCombine", "webkitTextDecorationsInEffect", "webkitTextEmphasis",
    "webkitTextEmphasisColor", "webkitTextEmphasisPosition", "webkitTextEmphasisStyle", "webkitTextFillColor",
    "webkitTextOrientation", "webkitTextSecurity", "webkitTextSizeAdjust", "webkitTextStroke", "webkitTextStrokeColor",
    "webkitTextStrokeWidth", "webkitTransform", "webkitTransformOrigin", "webkitTransformOriginX",
    "webkitTransformOriginY", "webkitTransformOriginZ", "webkitTransformStyle", "webkitTransition",
    "webkitTransitionDelay", "webkitTransitionDuration", "webkitTransitionProperty", "webkitTransitionTimingFunction",
    "webkitUserDrag", "webkitUserModify", "webkitUserSelect", "webkitWritingMode", "whiteSpace", "whiteSpaceCollapse",
    "widows", "width", "willChange", "wordBreak", "wordSpacing", "wordWrap", "writingMode", "x", "y", "zIndex", "zoom",
)


def _canonical(name):
    """The dashed, lower-case form every spelling of a property maps onto."""
    return _dash_name(name).lstrip("-").lower()


SUPPORTED_NAMES = frozenset(_canonical(name) for name in _CAMEL_NAMES)


def supported(name):
    """Whether a camel-case, dashed or prefixed spelling names a real property."""
    if name.startswith("--"):
        return True
    return _canonical(name) in SUPPORTED_NAMES


#: Properties whose computed value is inherited from the parent element.
INHERITED_PROPERTIES = frozenset((
    "color", "font-family", "font-size", "font-style", "font-weight",
    "line-height", "letter-spacing", "word-spacing", "visibility", "white-space",
))

#: The initial value the cascade starts from for each supported property.
INITIAL_VALUES = {
    "display": "block", "position": "static", "visibility": "visible",
    "font-family": "sans-serif", "font-size": "16px", "font-weight": "400",
    "font-style": "normal", "line-height": "normal", "letter-spacing": "normal",
    "word-spacing": "normal", "box-sizing": "content-box",
    "color": "rgb(0, 0, 0)", "white-space": "normal",
}

#: The display a tag starts from, measured from Chrome 153's user-agent sheet.
TAG_DISPLAY = {
    "a": "inline", "body": "block", "button": "inline-block", "canvas": "inline",
    "div": "block", "form": "block", "head": "none", "html": "block",
    "iframe": "inline", "img": "inline", "input": "inline-block", "link": "none",
    "meta": "none", "option": "block", "script": "none", "select": "inline-block",
    "span": "inline", "style": "none", "table": "table", "td": "table-cell",
    "template": "none", "textarea": "inline-block", "tr": "table-row",
}

#: What an element with no entry in :data:`TAG_DISPLAY` starts from.
UNTAGGED_DISPLAY = "inline"


def initial_values_for(element):
    """The cascade's starting values for ``element``; ``display`` depends on the tag."""
    values = dict(INITIAL_VALUES)
    values["display"] = TAG_DISPLAY.get(getattr(element, "localName", ""), UNTAGGED_DISPLAY)
    return values


def parse_declarations(raw):
    """Parse ``a: b; c: d !important`` into a declaration list.

    A repeated property keeps the position of its first occurrence and the value
    of its last; this is the single parser for ``cssText`` and the cascade reader.
    """
    declarations, positions = [], {}
    for chunk in str(raw).split(";"):
        if ":" not in chunk:
            continue
        name, value = chunk.split(":", 1)
        name = _property_name(name)
        if not name:
            continue
        value, priority = _split_priority(_normalize_value(value))
        if not value:
            continue
        item = (name, value, priority)
        if name in positions:
            declarations[positions[name]] = item
        else:
            positions[name] = len(declarations)
            declarations.append(item)
    return declarations


@interface("CSSStyleDeclaration", None)
class CSSStyleDeclaration:
    __v8_idl_spec__ = IdlSpec(
        attributes=(AttributeSpec(name="cssText", getter="get_cssText", setter="set_cssText"),
                    AttributeSpec(name="length", getter="get_length", side_effect=1),
                    AttributeSpec(name="parentRule", getter="get_parentRule", side_effect=1),
                    AttributeSpec(name="cssFloat", getter="get_cssFloat", setter="set_cssFloat")),
        methods=(MethodSpec(name="getPropertyPriority", callback="fn_getPropertyPriority", length=1, side_effect=1),
                 MethodSpec(name="getPropertyValue", callback="fn_getPropertyValue", length=1, side_effect=1),
                 MethodSpec(name="item", callback="fn_item", length=1, side_effect=1),
                 MethodSpec(name="removeProperty", callback="fn_removeProperty", length=1),
                 MethodSpec(name="setProperty", callback="fn_setProperty", length=2)),
        constructor_behavior=0, has_constructor=1, to_string_tag="CSSStyleDeclaration", array=True,
        name_get="__v8_name_get__", name_set="__v8_name_set__", name_query="__v8_name_query__",
        name_del="__v8_name_del__", name_enum="__v8_name_enum__",
        index_get="__v8_index_get__", index_set="__v8_index_set__", index_query="__v8_index_query__",
        index_del="__v8_index_del__", index_enum="__v8_index_enum__",
    )
    def __init__(self, element=None, **_): self._element = element
    def _declarations(self):
        if hasattr(self, "_computed_values"):
            return [(name, value, "") for name, value in self._computed_values.items() if value not in ("", None)]
        raw = str(self._element.attributes.get("style", "")) if self._element is not None else ""
        return parse_declarations(raw)
    def _write(self, declarations):
        if self._element is None: return
        text = "".join(f"{n}: {v}{' !important' if p else ''}; " for n, v, p in declarations).strip()
        # Chromium leaves the attribute behind and empties it, so removing the last
        # declaration reads back as "" rather than null.
        if text: self._element.attributes["style"] = text
        elif "style" in self._element.attributes: self._element.attributes["style"] = ""
    def _set(self, name, value, priority=""):
        name = _property_name(name)
        if not name: return
        priority = str(dom_string(priority)).strip().lower()
        if priority and priority != "important": return
        value = _normalize_value(value); priority = "important" if priority == "important" else ""
        declarations = [item for item in self._declarations() if item[0] != name]
        if value: declarations.append((name, value, priority))
        self._write(declarations)
    def _value(self, name):
        name = _property_name(name)
        return next((v for n, v, _ in self._declarations() if n == name), "")
    def get_cssText(self):
        return "".join(f"{n}: {v}{' !important' if p else ''}; " for n, v, p in self._declarations()).strip()
    def set_cssText(self, value):
        self._write(parse_declarations(dom_string(value)))
    def get_length(self): return len(self._declarations())
    def get_parentRule(self): return pyv8.Null
    def get_cssFloat(self): return self._value("float")
    def set_cssFloat(self, value): self._set("float", value)
    def fn_getPropertyPriority(self, name=MISSING, *_extra):
        if name is MISSING: raise missing_error("CSSStyleDeclaration", "getPropertyPriority", 1, 0)
        name = _property_name(name); return next((p for n, _, p in self._declarations() if n == name), "")
    def fn_getPropertyValue(self, name=MISSING, *_extra):
        if name is MISSING: raise missing_error("CSSStyleDeclaration", "getPropertyValue", 1, 0)
        return self._value(name)
    def fn_item(self, index=MISSING, *_extra):
        if index is MISSING: raise missing_error("CSSStyleDeclaration", "item", 1, 0)
        index = integer(index); declarations = self._declarations()
        return declarations[index][0] if 0 <= index < len(declarations) else ""
    def fn_removeProperty(self, name=MISSING, *_extra):
        if name is MISSING: raise missing_error("CSSStyleDeclaration", "removeProperty", 1, 0)
        name = _property_name(name)
        declarations = self._declarations()
        old = next((v for n, v, _ in declarations if n == name), "")
        self._write([item for item in declarations if item[0] != name]); return old
    def fn_setProperty(self, name=MISSING, value=MISSING, priority="", *_extra):
        if name is MISSING: raise missing_error("CSSStyleDeclaration", "setProperty", 2, 0)
        if value is MISSING: raise missing_error("CSSStyleDeclaration", "setProperty", 2, 1)
        self._set(name, value, priority)
    def _named(self, name):
        if not isinstance(name, str) or name in _OBJECT_NAMES: return None
        dashed = _dash_name(name)
        if not _NAME_RE.match(dashed): return None
        value = self._value(dashed)
        if value: return value
        # A supported but undeclared property reads as ""; an unknown name stays undefined.
        return "" if supported(dashed) else None
    def _exists(self, name):
        """Whether the engine resolves this name to a property at all."""
        dashed = _dash_name(name)
        if not _NAME_RE.match(dashed): return False
        if supported(dashed): return True
        declared = _property_name(dashed)
        return any(_property_name(declared_name) == declared for declared_name, _, _ in self._declarations())
    def __v8_name_get__(self, name): return self._named(name)
    def __v8_name_set__(self, name, value):
        if not isinstance(name, str) or name in _OBJECT_NAMES: return None
        dashed = _dash_name(name)
        if not _NAME_RE.match(dashed): return None
        self._set(dashed, value); return True
    def __v8_name_query__(self, name):
        if not isinstance(name, str) or name in _OBJECT_NAMES: return None
        return 0 if self._exists(name) else None
    def __v8_name_del__(self, name):
        if isinstance(name, str) and name not in _OBJECT_NAMES: self._set(_dash_name(name), "")
        return True
    def __v8_name_enum__(self): return [_camel_name(name) for name, _, _ in self._declarations()]
    def __v8_index_get__(self, index): return self.fn_item(index)
    def __v8_index_set__(self, index, value): return None
    def __v8_index_query__(self, index): return V8Attribute.ReadOnly if self.fn_item(index) else None
    def __v8_index_del__(self, index): return None
    def __v8_index_enum__(self): return self.get_length()

@interface('StyleSheet', None)
class StyleSheet:
    """One stylesheet as a page sees it: where it came from, and what it is for."""

    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="type", getter="get_type", side_effect=1),
            AttributeSpec(name="href", getter="get_href", side_effect=1),
            AttributeSpec(name="title", getter="get_title", side_effect=1),
            AttributeSpec(name="disabled", getter="get_disabled", setter="set_disabled"),
            AttributeSpec(name="ownerNode", getter="get_ownerNode", side_effect=1),
            AttributeSpec(name="parentStyleSheet", getter="get_parentStyleSheet", side_effect=1),
        ),
        constructor_behavior=0,
        to_string_tag="StyleSheet",
        has_constructor=0,
    )

    def __init__(self, owner=None, document=None, **_):
        self._owner, self._document = owner, document

    def get_type(self): return "text/css"
    def get_href(self):
        """Where a linked sheet came from; a ``<style>`` element has no URL."""
        if self._owner is None or str(getattr(self._owner, "localName", "")).lower() != "link":
            return pyv8.Null
        href = self._owner.attributes.get("href")
        if href is None:
            return pyv8.Null
        return urljoin(getattr(self._document, "url", "") or "", str(href))
    def get_title(self):
        title = None if self._owner is None else self._owner.attributes.get("title")
        return pyv8.Null if title is None else str(title)
    def get_disabled(self):
        return bool(self._owner is not None and "disabled" in self._owner.attributes)
    def set_disabled(self, value):
        if self._owner is None:
            return
        if boolean(value):
            self._owner.attributes["disabled"] = ""
        else:
            self._owner.attributes.pop("disabled", None)
    def get_ownerNode(self):
        return pyv8.Null if self._owner is None else self._owner
    def get_parentStyleSheet(self):
        return pyv8.Null


@interface('CSSStyleSheet', 'StyleSheet')
class CSSStyleSheet(StyleSheet):
    """A sheet of CSS rules. The rules themselves are the baseline's business here;
    what a page reads first is the owner it came from and whether it is disabled."""

    __v8_idl_spec__ = IdlSpec(
        attributes=(),
        constructor_behavior=0,
        to_string_tag="CSSStyleSheet",
        has_constructor=0,
    )


@interface('StyleSheetList', None)
class StyleSheetList(ReadOnlyIndexedPropertyMixin, LegacyNamedPropertyMixin):
    """The document's sheets, live: ``<style>`` elements and the ``<link>`` elements
    that are stylesheets, in document order."""

    __v8_idl_spec__ = IdlSpec(
        attributes=(AttributeSpec(name="length", getter="get_length", side_effect=1),),
        methods=(
            MethodSpec(name="item", callback="fn_item", length=1, side_effect=1),
        ),
        constructor_behavior=0,
        to_string_tag="StyleSheetList",
        has_constructor=1,
    )

    def __init__(self, document=None, **_):
        self._document = document

    def _owners(self):
        owners = []
        for node in self._document._descendants():
            name = str(getattr(node, "localName", "") or "").lower()
            rel = str(getattr(node, "attributes", {}).get("rel", "")).lower().split()
            if name == "style" or (name == "link" and "stylesheet" in rel):
                owners.append(node)
        return owners
    def _sheet_for(self, node):
        """The sheet for one owner, kept on the node so a second read is the same
        object, as it is in Chrome.
        """
        sheet = getattr(node, "_style_sheet", None)
        if sheet is None:
            sheet = node._style_sheet = CSSStyleSheet(node, self._document)
        return sheet
    def get_length(self): return len(self._owners())
    def _indexed_items(self): return [self._sheet_for(node) for node in self._owners()]
    def fn_item(self, index=MISSING, *_extra):
        if index is MISSING:
            raise missing_error('StyleSheetList', 'item', 1, 0, constructor=False)
        items = self._indexed_items()
        position = int(index)
        return items[position] if 0 <= position < len(items) else pyv8.Null
    def _supported_names(self):
        return [str(node.attributes["id"]) for node in self._owners()
                if node.attributes.get("id")]
    def _named_item(self, name):
        for node in self._owners():
            if node.attributes.get("id") == name:
                return self._sheet_for(node)
        return pyv8.Null


__all__ = ["CSSStyleDeclaration", "CSSStyleSheet", "StyleSheet", "StyleSheetList",
           "SUPPORTED_NAMES", "parse_declarations", "supported"]
