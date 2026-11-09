"""CSS property names, initial values and inheritance."""
from ..dom.utils import dash_name as _dash_name

# ``'color' in style`` only answers for properties the engine supports, so the
# list matches what Chrome reports: ``-webkit-`` forms because Blink ships them,
# no ``-moz-``/``-ms-``/``-o-``, and custom properties always accepted.


#: camelCase, as reported by a real Chrome CSSStyleDeclaration, in the order it
#: enumerates them: a declaration moves no name and a custom property is not listed.
_CAMEL_NAMES = (
    "accentColor", "additiveSymbols", "alignContent", "alignItems", "alignSelf", "alignmentBaseline", "all",
    "anchorName", "anchorScope", "animation", "animationComposition", "animationDelay", "animationDirection",
    "animationDuration", "animationFillMode", "animationIterationCount", "animationName", "animationPlayState",
    "animationRange", "animationRangeEnd", "animationRangeStart", "animationTimeline",
    "animationTimingFunction", "animationTrigger", "appRegion", "appearance", "ascentOverride", "aspectRatio",
    "backdropFilter", "backfaceVisibility", "background", "backgroundAttachment", "backgroundBlendMode",
    "backgroundClip", "backgroundColor", "backgroundImage", "backgroundOrigin", "backgroundPosition",
    "backgroundPositionX", "backgroundPositionY", "backgroundRepeat", "backgroundSize", "basePalette",
    "baselineShift", "baselineSource", "blockSize", "border", "borderBlock", "borderBlockColor",
    "borderBlockEnd", "borderBlockEndColor", "borderBlockEndStyle", "borderBlockEndWidth", "borderBlockStart",
    "borderBlockStartColor", "borderBlockStartStyle", "borderBlockStartWidth", "borderBlockStyle",
    "borderBlockWidth", "borderBottom", "borderBottomColor", "borderBottomLeftRadius",
    "borderBottomRightRadius", "borderBottomStyle", "borderBottomWidth", "borderCollapse", "borderColor",
    "borderEndEndRadius", "borderEndStartRadius", "borderImage", "borderImageOutset", "borderImageRepeat",
    "borderImageSlice", "borderImageSource", "borderImageWidth", "borderInline", "borderInlineColor",
    "borderInlineEnd", "borderInlineEndColor", "borderInlineEndStyle", "borderInlineEndWidth",
    "borderInlineStart", "borderInlineStartColor", "borderInlineStartStyle", "borderInlineStartWidth",
    "borderInlineStyle", "borderInlineWidth", "borderLeft", "borderLeftColor", "borderLeftStyle",
    "borderLeftWidth", "borderRadius", "borderRight", "borderRightColor", "borderRightStyle",
    "borderRightWidth", "borderShape", "borderSpacing", "borderStartEndRadius", "borderStartStartRadius",
    "borderStyle", "borderTop", "borderTopColor", "borderTopLeftRadius", "borderTopRightRadius",
    "borderTopStyle", "borderTopWidth", "borderWidth", "bottom", "boxDecorationBreak", "boxShadow",
    "boxSizing", "breakAfter", "breakBefore", "breakInside", "bufferedRendering", "captionSide",
    "caretAnimation", "caretColor", "caretShape", "clear", "clip", "clipPath", "clipRule", "color",
    "colorInterpolation", "colorInterpolationFilters", "colorRendering", "colorScheme", "columnCount",
    "columnFill", "columnGap", "columnHeight", "columnRule", "columnRuleBreak", "columnRuleColor",
    "columnRuleInset", "columnRuleInsetCap", "columnRuleInsetCapEnd", "columnRuleInsetCapStart",
    "columnRuleInsetEnd", "columnRuleInsetJunction", "columnRuleInsetJunctionEnd",
    "columnRuleInsetJunctionStart", "columnRuleInsetStart", "columnRuleStyle", "columnRuleVisibilityItems",
    "columnRuleWidth", "columnSpan", "columnWidth", "columnWrap", "columns", "contain",
    "containIntrinsicBlockSize", "containIntrinsicHeight", "containIntrinsicInlineSize",
    "containIntrinsicSize", "containIntrinsicWidth", "container", "containerName", "containerType", "content",
    "contentVisibility", "cornerBlockEndShape", "cornerBlockStartShape", "cornerBottomLeftShape",
    "cornerBottomRightShape", "cornerBottomShape", "cornerEndEndShape", "cornerEndStartShape",
    "cornerInlineEndShape", "cornerInlineStartShape", "cornerLeftShape", "cornerRightShape", "cornerShape",
    "cornerStartEndShape", "cornerStartStartShape", "cornerTopLeftShape", "cornerTopRightShape",
    "cornerTopShape", "counterIncrement", "counterReset", "counterSet", "cursor", "cx", "cy", "d",
    "descentOverride", "direction", "display", "dominantBaseline", "dynamicRangeLimit", "emptyCells",
    "fallback", "fieldSizing", "fill", "fillOpacity", "fillRule", "filter", "flex", "flexBasis",
    "flexDirection", "flexFlow", "flexGrow", "flexLineCount", "flexShrink", "flexWrap", "float", "floodColor",
    "floodOpacity", "font", "fontDisplay", "fontFamily", "fontFeatureSettings", "fontKerning",
    "fontLanguageOverride", "fontOpticalSizing", "fontPalette", "fontSize", "fontSizeAdjust", "fontStretch",
    "fontStyle", "fontSynthesis", "fontSynthesisSmallCaps", "fontSynthesisStyle", "fontSynthesisWeight",
    "fontVariant", "fontVariantAlternates", "fontVariantCaps", "fontVariantEastAsian", "fontVariantEmoji",
    "fontVariantLigatures", "fontVariantNumeric", "fontVariantPosition", "fontVariationSettings", "fontWeight",
    "forcedColorAdjust", "frameSizing", "gap", "grid", "gridArea", "gridAutoColumns", "gridAutoFlow",
    "gridAutoRows", "gridColumn", "gridColumnEnd", "gridColumnGap", "gridColumnStart", "gridGap", "gridRow",
    "gridRowEnd", "gridRowGap", "gridRowStart", "gridTemplate", "gridTemplateAreas", "gridTemplateColumns",
    "gridTemplateRows", "height", "hyphenateCharacter", "hyphenateLimitChars", "hyphens", "imageOrientation",
    "imageRendering", "inherits", "initialLetter", "initialValue", "inlineSize", "inset", "insetBlock",
    "insetBlockEnd", "insetBlockStart", "insetInline", "insetInlineEnd", "insetInlineStart", "interactivity",
    "interestDelay", "interestDelayEnd", "interestDelayStart", "interpolateSize", "isolation",
    "justifyContent", "justifyItems", "justifySelf", "left", "letterSpacing", "lightingColor", "lineBreak",
    "lineGapOverride", "lineHeight", "listStyle", "listStyleImage", "listStylePosition", "listStyleType",
    "margin", "marginBlock", "marginBlockEnd", "marginBlockStart", "marginBottom", "marginInline",
    "marginInlineEnd", "marginInlineStart", "marginLeft", "marginRight", "marginTop", "marker", "markerEnd",
    "markerMid", "markerStart", "mask", "maskClip", "maskComposite", "maskImage", "maskMode", "maskOrigin",
    "maskPosition", "maskRepeat", "maskSize", "maskType", "mathDepth", "mathShift", "mathStyle",
    "maxBlockSize", "maxHeight", "maxInlineSize", "maxWidth", "minBlockSize", "minHeight", "minInlineSize",
    "minWidth", "mixBlendMode", "navigation", "negative", "objectFit", "objectPosition", "objectViewBox",
    "offset", "offsetAnchor", "offsetDistance", "offsetPath", "offsetPosition", "offsetRotate", "opacity",
    "order", "orphans", "outline", "outlineColor", "outlineOffset", "outlineStyle", "outlineWidth", "overflow",
    "overflowAnchor", "overflowBlock", "overflowClipMargin", "overflowInline", "overflowWrap", "overflowX",
    "overflowY", "overlay", "overrideColors", "overscrollBehavior", "overscrollBehaviorBlock",
    "overscrollBehaviorInline", "overscrollBehaviorX", "overscrollBehaviorY", "pad", "padding", "paddingBlock",
    "paddingBlockEnd", "paddingBlockStart", "paddingBottom", "paddingInline", "paddingInlineEnd",
    "paddingInlineStart", "paddingLeft", "paddingRight", "paddingTop", "page", "pageBreakAfter",
    "pageBreakBefore", "pageBreakInside", "pageMarginSafety", "pageOrientation", "paintOrder", "perspective",
    "perspectiveOrigin", "placeContent", "placeItems", "placeSelf", "pointerEvents", "position",
    "positionAnchor", "positionArea", "positionTry", "positionTryFallbacks", "positionTryOrder",
    "positionVisibility", "prefix", "printColorAdjust", "quotes", "r", "range", "readingFlow", "readingOrder",
    "resize", "result", "right", "rotate", "rowGap", "rowRule", "rowRuleBreak", "rowRuleColor", "rowRuleInset",
    "rowRuleInsetCap", "rowRuleInsetCapEnd", "rowRuleInsetCapStart", "rowRuleInsetEnd", "rowRuleInsetJunction",
    "rowRuleInsetJunctionEnd", "rowRuleInsetJunctionStart", "rowRuleInsetStart", "rowRuleStyle",
    "rowRuleVisibilityItems", "rowRuleWidth", "rubyAlign", "rubyOverhang", "rubyPosition", "rule", "ruleBreak",
    "ruleColor", "ruleInset", "ruleInsetCap", "ruleInsetEnd", "ruleInsetJunction", "ruleInsetStart",
    "ruleOverlap", "ruleStyle", "ruleVisibilityItems", "ruleWidth", "rx", "ry", "scale", "scrollAxisLock",
    "scrollBehavior", "scrollInitialTarget", "scrollMargin", "scrollMarginBlock", "scrollMarginBlockEnd",
    "scrollMarginBlockStart", "scrollMarginBottom", "scrollMarginInline", "scrollMarginInlineEnd",
    "scrollMarginInlineStart", "scrollMarginLeft", "scrollMarginRight", "scrollMarginTop", "scrollMarkerGroup",
    "scrollPadding", "scrollPaddingBlock", "scrollPaddingBlockEnd", "scrollPaddingBlockStart",
    "scrollPaddingBottom", "scrollPaddingInline", "scrollPaddingInlineEnd", "scrollPaddingInlineStart",
    "scrollPaddingLeft", "scrollPaddingRight", "scrollPaddingTop", "scrollSnapAlign", "scrollSnapStop",
    "scrollSnapType", "scrollTargetGroup", "scrollTimeline", "scrollTimelineAxis", "scrollTimelineName",
    "scrollbarColor", "scrollbarGutter", "scrollbarWidth", "shapeImageThreshold", "shapeMargin",
    "shapeOutside", "shapeRendering", "size", "sizeAdjust", "speak", "speakAs", "src", "stopColor",
    "stopOpacity", "stroke", "strokeDasharray", "strokeDashoffset", "strokeLinecap", "strokeLinejoin",
    "strokeMiterlimit", "strokeOpacity", "strokeWidth", "suffix", "symbols", "syntax", "system", "tabSize",
    "tableLayout", "textAlign", "textAlignLast", "textAnchor", "textAutospace", "textBox", "textBoxEdge",
    "textBoxTrim", "textCombineUpright", "textDecoration", "textDecorationColor", "textDecorationLine",
    "textDecorationSkipInk", "textDecorationStyle", "textDecorationThickness", "textEmphasis",
    "textEmphasisColor", "textEmphasisPosition", "textEmphasisStyle", "textFit", "textIndent", "textJustify",
    "textOrientation", "textOverflow", "textRendering", "textShadow", "textSizeAdjust", "textSpacingTrim",
    "textTransform", "textUnderlineOffset", "textUnderlinePosition", "textWrap", "textWrapMode",
    "textWrapStyle", "timelineScope", "timelineTrigger", "timelineTriggerActivationRange",
    "timelineTriggerActivationRangeEnd", "timelineTriggerActivationRangeStart", "timelineTriggerActiveRange",
    "timelineTriggerActiveRangeEnd", "timelineTriggerActiveRangeStart", "timelineTriggerName",
    "timelineTriggerSource", "top", "touchAction", "transform", "transformBox", "transformOrigin",
    "transformStyle", "transition", "transitionBehavior", "transitionDelay", "transitionDuration",
    "transitionProperty", "transitionTimingFunction", "translate", "triggerScope", "types", "unicodeBidi",
    "unicodeRange", "userSelect", "vectorEffect", "verticalAlign", "viewTimeline", "viewTimelineAxis",
    "viewTimelineInset", "viewTimelineName", "viewTransitionClass", "viewTransitionGroup",
    "viewTransitionName", "viewTransitionScope", "visibility", "webkitAlignContent", "webkitAlignItems",
    "webkitAlignSelf", "webkitAnimation", "webkitAnimationDelay", "webkitAnimationDirection",
    "webkitAnimationDuration", "webkitAnimationFillMode", "webkitAnimationIterationCount",
    "webkitAnimationName", "webkitAnimationPlayState", "webkitAnimationTimingFunction", "webkitAppRegion",
    "webkitAppearance", "webkitBackfaceVisibility", "webkitBackgroundClip", "webkitBackgroundOrigin",
    "webkitBackgroundSize", "webkitBorderAfter", "webkitBorderAfterColor", "webkitBorderAfterStyle",
    "webkitBorderAfterWidth", "webkitBorderBefore", "webkitBorderBeforeColor", "webkitBorderBeforeStyle",
    "webkitBorderBeforeWidth", "webkitBorderBottomLeftRadius", "webkitBorderBottomRightRadius",
    "webkitBorderEnd", "webkitBorderEndColor", "webkitBorderEndStyle", "webkitBorderEndWidth",
    "webkitBorderHorizontalSpacing", "webkitBorderImage", "webkitBorderRadius", "webkitBorderStart",
    "webkitBorderStartColor", "webkitBorderStartStyle", "webkitBorderStartWidth", "webkitBorderTopLeftRadius",
    "webkitBorderTopRightRadius", "webkitBorderVerticalSpacing", "webkitBoxAlign", "webkitBoxDecorationBreak",
    "webkitBoxDirection", "webkitBoxFlex", "webkitBoxOrdinalGroup", "webkitBoxOrient", "webkitBoxPack",
    "webkitBoxReflect", "webkitBoxShadow", "webkitBoxSizing", "webkitClipPath", "webkitColumnBreakAfter",
    "webkitColumnBreakBefore", "webkitColumnBreakInside", "webkitColumnCount", "webkitColumnGap",
    "webkitColumnRule", "webkitColumnRuleColor", "webkitColumnRuleStyle", "webkitColumnRuleWidth",
    "webkitColumnSpan", "webkitColumnWidth", "webkitColumns", "webkitFilter", "webkitFlex", "webkitFlexBasis",
    "webkitFlexDirection", "webkitFlexFlow", "webkitFlexGrow", "webkitFlexShrink", "webkitFlexWrap",
    "webkitFontFeatureSettings", "webkitFontSmoothing", "webkitHyphenateCharacter", "webkitJustifyContent",
    "webkitLineBreak", "webkitLineClamp", "webkitLocale", "webkitLogicalHeight", "webkitLogicalWidth",
    "webkitMarginAfter", "webkitMarginBefore", "webkitMarginEnd", "webkitMarginStart", "webkitMask",
    "webkitMaskBoxImage", "webkitMaskBoxImageOutset", "webkitMaskBoxImageRepeat", "webkitMaskBoxImageSlice",
    "webkitMaskBoxImageSource", "webkitMaskBoxImageWidth", "webkitMaskClip", "webkitMaskComposite",
    "webkitMaskImage", "webkitMaskOrigin", "webkitMaskPosition", "webkitMaskPositionX", "webkitMaskPositionY",
    "webkitMaskRepeat", "webkitMaskSize", "webkitMaxLogicalHeight", "webkitMaxLogicalWidth",
    "webkitMinLogicalHeight", "webkitMinLogicalWidth", "webkitOpacity", "webkitOrder", "webkitPaddingAfter",
    "webkitPaddingBefore", "webkitPaddingEnd", "webkitPaddingStart", "webkitPerspective",
    "webkitPerspectiveOrigin", "webkitPerspectiveOriginX", "webkitPerspectiveOriginY",
    "webkitPrintColorAdjust", "webkitRtlOrdering", "webkitRubyPosition", "webkitShapeImageThreshold",
    "webkitShapeMargin", "webkitShapeOutside", "webkitTapHighlightColor", "webkitTextCombine",
    "webkitTextDecorationsInEffect", "webkitTextEmphasis", "webkitTextEmphasisColor",
    "webkitTextEmphasisPosition", "webkitTextEmphasisStyle", "webkitTextFillColor", "webkitTextOrientation",
    "webkitTextSecurity", "webkitTextSizeAdjust", "webkitTextStroke", "webkitTextStrokeColor",
    "webkitTextStrokeWidth", "webkitTransform", "webkitTransformOrigin", "webkitTransformOriginX",
    "webkitTransformOriginY", "webkitTransformOriginZ", "webkitTransformStyle", "webkitTransition",
    "webkitTransitionDelay", "webkitTransitionDuration", "webkitTransitionProperty",
    "webkitTransitionTimingFunction", "webkitUserDrag", "webkitUserModify", "webkitUserSelect",
    "webkitWritingMode", "whiteSpace", "whiteSpaceCollapse", "widows", "width", "willChange", "windowDrag",
    "wordBreak", "wordSpacing", "wordWrap", "writingMode", "x", "y", "zIndex", "zoom", "cssText", "length",
    "parentRule", "cssFloat", "getPropertyPriority", "getPropertyValue", "item", "removeProperty",
    "setProperty"
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
    "word-break", "overflow-wrap", "text-align",
))


#: The initial value the cascade starts from for each supported property.
INITIAL_VALUES = {
    "display": "block", "position": "static", "visibility": "visible",
    "font-family": "sans-serif", "font-size": "16px", "font-weight": "400",
    "font-style": "normal", "line-height": "normal", "letter-spacing": "normal",
    "word-spacing": "normal", "box-sizing": "content-box",
    "color": "rgb(0, 0, 0)", "white-space": "normal",
    "word-break": "normal", "overflow-wrap": "normal", "text-align": "start",
}


#: The display a tag starts from, measured from Chrome 153's user-agent sheet.
TAG_DISPLAY = {
    "a": "inline", "address": "block", "article": "block", "aside": "block",
    "blockquote": "block", "body": "block", "button": "inline-block", "canvas": "inline",
    "center": "block",
    "dd": "block", "div": "block", "dl": "block", "dt": "block", "fieldset": "block",
    "figcaption": "block", "figure": "block", "footer": "block", "form": "block",
    "h1": "block", "h2": "block", "h3": "block", "h4": "block", "h5": "block",
    "h6": "block", "head": "none", "header": "block", "hgroup": "block", "hr": "block",
    "html": "block", "iframe": "inline", "img": "inline", "input": "inline-block",
    "legend": "block", "li": "list-item", "link": "none", "main": "block",
    "menu": "block", "meta": "none", "nav": "block", "ol": "block", "option": "block",
    "p": "block", "pre": "block", "script": "none", "section": "block",
    "select": "inline-block", "span": "inline", "style": "none", "summary": "list-item",
    "table": "table", "td": "table-cell", "template": "none", "textarea": "inline-block",
    "tr": "table-row", "ul": "block",
}


#: What an element with no entry in :data:`TAG_DISPLAY` starts from.
UNTAGGED_DISPLAY = "inline"


#: Box defaults the user-agent sheet gives a tag, measured from Chrome 154 in px at
#: the 16px root font size it starts from.
TAG_BOX_INITIALS = {
    "body": {"margin": "8px"},
    "iframe": {"border-width": "2px", "border-style": "inset"},
    "p": {"margin": "16px 0"},
    "blockquote": {"margin": "16px 40px"},
    "figure": {"margin": "16px 40px"},
    "dl": {"margin": "16px 0"},
    "ul": {"margin": "16px 0", "padding-left": "40px"},
    "ol": {"margin": "16px 0", "padding-left": "40px"},
    "dd": {"margin-left": "40px"},
    "pre": {"margin": "13px 0"},
    "hr": {"margin": "8px 0"},
    "h1": {"margin": "21.44px 0"},
    "h2": {"margin": "19.92px 0"},
    "h3": {"margin": "18.72px 0"},
    "h4": {"margin": "21.28px 0"},
    "h5": {"margin": "22.27px 0"},
    "h6": {"margin": "23.17px 0"},
}


def initial_values_for(element):
    """The cascade's starting values for ``element``; ``display`` depends on the tag."""
    tag = getattr(element, "localName", "")
    values = dict(INITIAL_VALUES)
    values["display"] = TAG_DISPLAY.get(tag, UNTAGGED_DISPLAY)
    values.update(TAG_BOX_INITIALS.get(tag, {}))
    return values


