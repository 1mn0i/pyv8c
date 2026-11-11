"""WebGL constants implementation."""

from pyv8c import pyv8


PARAMETERS = (
    ("BLEND_EQUATION", 32777, 32774),
    ("BLEND_EQUATION_RGB", 32777, 32774),
    ("BLEND_EQUATION_ALPHA", 34877, 32774),
    ("BLEND_DST_RGB", 32968, 0),
    ("BLEND_SRC_RGB", 32969, 1),
    ("BLEND_DST_ALPHA", 32970, 0),
    ("BLEND_SRC_ALPHA", 32971, 1),
    ("CULL_FACE", 2884, False),
    ("BLEND", 3042, False),
    ("DITHER", 3024, True),
    ("STENCIL_TEST", 2960, False),
    ("DEPTH_TEST", 2929, False),
    ("SCISSOR_TEST", 3089, False),
    ("POLYGON_OFFSET_FILL", 32823, False),
    ("SAMPLE_ALPHA_TO_COVERAGE", 32926, False),
    ("SAMPLE_COVERAGE", 32928, False),
    ("LINE_WIDTH", 2849, 1),
    ("CULL_FACE_MODE", 2885, 1029),
    ("FRONT_FACE", 2886, 2305),
    ("DEPTH_WRITEMASK", 2930, True),
    ("DEPTH_CLEAR_VALUE", 2931, 1),
    ("DEPTH_FUNC", 2932, 513),
    ("STENCIL_CLEAR_VALUE", 2961, 0),
    ("STENCIL_FUNC", 2962, 519),
    ("STENCIL_FAIL", 2964, 7680),
    ("STENCIL_PASS_DEPTH_FAIL", 2965, 7680),
    ("STENCIL_PASS_DEPTH_PASS", 2966, 7680),
    ("STENCIL_REF", 2967, 0),
    ("STENCIL_VALUE_MASK", 2963, 4294967295),
    ("STENCIL_WRITEMASK", 2968, 4294967295),
    ("STENCIL_BACK_FUNC", 34816, 519),
    ("STENCIL_BACK_FAIL", 34817, 7680),
    ("STENCIL_BACK_PASS_DEPTH_FAIL", 34818, 7680),
    ("STENCIL_BACK_PASS_DEPTH_PASS", 34819, 7680),
    ("STENCIL_BACK_REF", 36003, 0),
    ("STENCIL_BACK_VALUE_MASK", 36004, 4294967295),
    ("STENCIL_BACK_WRITEMASK", 36005, 4294967295),
    ("UNPACK_ALIGNMENT", 3317, 4),
    ("PACK_ALIGNMENT", 3333, 4),
    ("MAX_TEXTURE_SIZE", 3379, 16384),
    ("SUBPIXEL_BITS", 3408, 4),
    ("RED_BITS", 3410, 8),
    ("GREEN_BITS", 3411, 8),
    ("BLUE_BITS", 3412, 8),
    ("ALPHA_BITS", 3413, 8),
    ("DEPTH_BITS", 3414, 24),
    ("STENCIL_BITS", 3415, 0),
    ("POLYGON_OFFSET_UNITS", 10752, 0),
    ("POLYGON_OFFSET_FACTOR", 32824, 0),
    ("SAMPLE_BUFFERS", 32936, 1),
    ("SAMPLES", 32937, 4),
    ("SAMPLE_COVERAGE_VALUE", 32938, 1),
    ("SAMPLE_COVERAGE_INVERT", 32939, False),
    ("GENERATE_MIPMAP_HINT", 33170, 4352),
    ("MAX_VERTEX_ATTRIBS", 34921, 16),
    ("MAX_VERTEX_UNIFORM_VECTORS", 36347, 4095),
    ("MAX_VARYING_VECTORS", 36348, 30),
    ("MAX_COMBINED_TEXTURE_IMAGE_UNITS", 35661, 32),
    ("MAX_VERTEX_TEXTURE_IMAGE_UNITS", 35660, 16),
    ("MAX_TEXTURE_IMAGE_UNITS", 34930, 16),
    ("MAX_FRAGMENT_UNIFORM_VECTORS", 36349, 1024),
    ("MAX_CUBE_MAP_TEXTURE_SIZE", 34076, 16384),
    ("ACTIVE_TEXTURE", 34016, 33984),
    ("IMPLEMENTATION_COLOR_READ_TYPE", 35738, 5121),
    ("IMPLEMENTATION_COLOR_READ_FORMAT", 35739, 6408),
    ("MAX_RENDERBUFFER_SIZE", 34024, 16384),
    ("UNPACK_FLIP_Y_WEBGL", 37440, False),
    ("UNPACK_PREMULTIPLY_ALPHA_WEBGL", 37441, False),
    ("UNPACK_COLORSPACE_CONVERSION_WEBGL", 37443, 37444),
)


ARRAY_PARAMETERS = (
    ("BLEND_COLOR", 32773, pyv8.ArrayType.Float32Array, (0, 0, 0, 0)),
    ("ALIASED_POINT_SIZE_RANGE", 33901, pyv8.ArrayType.Float32Array, (1, 1024)),
    ("ALIASED_LINE_WIDTH_RANGE", 33902, pyv8.ArrayType.Float32Array, (1, 1)),
    ("DEPTH_RANGE", 2928, pyv8.ArrayType.Float32Array, (0, 1)),
    ("COLOR_CLEAR_VALUE", 3106, pyv8.ArrayType.Float32Array, (0, 0, 0, 0)),
    ("MAX_VIEWPORT_DIMS", 3386, pyv8.ArrayType.Int32Array, (32767, 32767)),
    ("VIEWPORT", 2978, pyv8.ArrayType.Int32Array, None),
    ("SCISSOR_BOX", 3088, pyv8.ArrayType.Int32Array, None),
    ("COMPRESSED_TEXTURE_FORMATS", 34467, pyv8.ArrayType.Uint32Array, ()),
)


STRINGS = (
    ("VERSION", 7938, "WebGL 1.0 (OpenGL ES 2.0 Chromium)"),
    ("VENDOR", 7936, "WebKit"),
    ("RENDERER", 7937, "WebKit WebGL"),
    ("SHADING_LANGUAGE_VERSION", 35724, "WebGL GLSL ES 1.0 (OpenGL ES GLSL ES 1.0 Chromium)"),
)


WEBGL2_STRINGS = (
    ("VERSION", 7938, "WebGL 2.0 (OpenGL ES 3.0 Chromium)"),
    ("VENDOR", 7936, "WebKit"),
    ("RENDERER", 7937, "WebKit WebGL"),
    ("SHADING_LANGUAGE_VERSION", 35724,
     "WebGL GLSL ES 3.00 (OpenGL ES GLSL ES 3.0 Chromium)"),
)


WEBGL2_PARAMETERS = (
    ("DRAW_BUFFER0", 34853, 1029),
    ("DRAW_BUFFER1", 34854, 1029),
    ("DRAW_BUFFER2", 34855, 1029),
    ("DRAW_BUFFER3", 34856, 1029),
    ("DRAW_BUFFER4", 34857, 1029),
    ("DRAW_BUFFER5", 34858, 1029),
    ("DRAW_BUFFER6", 34859, 1029),
    ("DRAW_BUFFER7", 34860, 1029),
    ("FRAGMENT_SHADER_DERIVATIVE_HINT", 35723, 4352),
    ("MAX_3D_TEXTURE_SIZE", 32883, 2048),
    ("MAX_ARRAY_TEXTURE_LAYERS", 35071, 2048),
    ("MAX_CLIENT_WAIT_TIMEOUT_WEBGL", 37447, 0),
    ("MAX_COLOR_ATTACHMENTS", 36063, 8),
    ("MAX_COMBINED_FRAGMENT_UNIFORM_COMPONENTS", 35379, 200704),
    ("MAX_COMBINED_UNIFORM_BLOCKS", 35374, 24),
    ("MAX_COMBINED_VERTEX_UNIFORM_COMPONENTS", 35377, 212988),
    ("MAX_DRAW_BUFFERS", 34852, 8),
    ("MAX_ELEMENTS_INDICES", 33001, 2147483647),
    ("MAX_ELEMENTS_VERTICES", 33000, 2147483647),
    ("MAX_ELEMENT_INDEX", 36203, 4294967294),
    ("MAX_FRAGMENT_INPUT_COMPONENTS", 37157, 120),
    ("MAX_FRAGMENT_UNIFORM_BLOCKS", 35373, 12),
    ("MAX_FRAGMENT_UNIFORM_COMPONENTS", 35657, 4096),
    ("MAX_PROGRAM_TEXEL_OFFSET", 35077, 7),
    ("MAX_SAMPLES", 36183, 8),
    ("MAX_SERVER_WAIT_TIMEOUT", 37137, 0),
    ("MAX_TEXTURE_LOD_BIAS", 34045, 2),
    ("MAX_TRANSFORM_FEEDBACK_INTERLEAVED_COMPONENTS", 35978, 120),
    ("MAX_TRANSFORM_FEEDBACK_SEPARATE_ATTRIBS", 35979, 4),
    ("MAX_TRANSFORM_FEEDBACK_SEPARATE_COMPONENTS", 35968, 4),
    ("MAX_UNIFORM_BLOCK_SIZE", 35376, 65536),
    ("MAX_UNIFORM_BUFFER_BINDINGS", 35375, 24),
    ("MAX_VARYING_COMPONENTS", 35659, 120),
    ("MAX_VERTEX_OUTPUT_COMPONENTS", 37154, 120),
    ("MAX_VERTEX_UNIFORM_BLOCKS", 35371, 12),
    ("MAX_VERTEX_UNIFORM_COMPONENTS", 35658, 16380),
    ("MIN_PROGRAM_TEXEL_OFFSET", 35076, -8),
    ("PACK_ROW_LENGTH", 3330, 0),
    ("PACK_SKIP_PIXELS", 3332, 0),
    ("PACK_SKIP_ROWS", 3331, 0),
    ("RASTERIZER_DISCARD", 35977, False),
    ("READ_BUFFER", 3074, 1029),
    ("TRANSFORM_FEEDBACK_ACTIVE", 36388, False),
    ("TRANSFORM_FEEDBACK_PAUSED", 36387, False),
    ("UNIFORM_BUFFER_OFFSET_ALIGNMENT", 35380, 256),
    ("UNPACK_IMAGE_HEIGHT", 32878, 0),
    ("UNPACK_ROW_LENGTH", 3314, 0),
    ("UNPACK_SKIP_IMAGES", 32877, 0),
    ("UNPACK_SKIP_PIXELS", 3316, 0),
    ("UNPACK_SKIP_ROWS", 3315, 0),
    ("COPY_READ_BUFFER", 36662, None),
    ("COPY_WRITE_BUFFER", 36663, None),
    ("PIXEL_PACK_BUFFER_BINDING", 35053, None),
    ("PIXEL_UNPACK_BUFFER_BINDING", 35055, None),
    ("READ_FRAMEBUFFER_BINDING", 36010, None),
    ("SAMPLER_BINDING", 35097, None),
    ("TEXTURE_BINDING_2D_ARRAY", 35869, None),
    ("TEXTURE_BINDING_3D", 32874, None),
    ("TRANSFORM_FEEDBACK_BINDING", 36389, None),
    ("TRANSFORM_FEEDBACK_BUFFER_BINDING", 35983, None),
    ("UNIFORM_BUFFER_BINDING", 35368, None),
    ("VERTEX_ARRAY_BINDING", 34229, None),
)


WEBGL1_EXTENSIONS = (
    ("ANGLE_instanced_arrays", "ANGLEInstancedArrays"),
    ("EXT_blend_minmax", "EXTBlendMinMax"),
    ("EXT_clip_control", "EXTClipControl"),
    ("EXT_color_buffer_half_float", "EXTColorBufferHalfFloat"),
    ("EXT_depth_clamp", "EXTDepthClamp"),
    ("EXT_disjoint_timer_query", "EXTDisjointTimerQuery"),
    ("EXT_float_blend", "EXTFloatBlend"),
    ("EXT_frag_depth", "EXTFragDepth"),
    ("EXT_polygon_offset_clamp", "EXTPolygonOffsetClamp"),
    ("EXT_shader_texture_lod", "EXTShaderTextureLOD"),
    ("EXT_texture_compression_bptc", "EXTTextureCompressionBPTC"),
    ("EXT_texture_compression_rgtc", "EXTTextureCompressionRGTC"),
    ("EXT_texture_filter_anisotropic", "EXTTextureFilterAnisotropic"),
    ("EXT_texture_mirror_clamp_to_edge", "EXTTextureMirrorClampToEdge"),
    ("EXT_sRGB", "EXTsRGB"),
    ("KHR_parallel_shader_compile", "KHRParallelShaderCompile"),
    ("OES_element_index_uint", "OESElementIndexUint"),
    ("OES_fbo_render_mipmap", "OESFboRenderMipmap"),
    ("OES_standard_derivatives", "OESStandardDerivatives"),
    ("OES_texture_float", "OESTextureFloat"),
    ("OES_texture_float_linear", "OESTextureFloatLinear"),
    ("OES_texture_half_float", "OESTextureHalfFloat"),
    ("OES_texture_half_float_linear", "OESTextureHalfFloatLinear"),
    ("OES_vertex_array_object", "OESVertexArrayObject"),
    ("WEBGL_blend_func_extended", "WebGLBlendFuncExtended"),
    ("WEBGL_color_buffer_float", "WebGLColorBufferFloat"),
    ("WEBGL_compressed_texture_s3tc", "WebGLCompressedTextureS3TC"),
    ("WEBGL_compressed_texture_s3tc_srgb", "WebGLCompressedTextureS3TCsRGB"),
    ("WEBGL_debug_renderer_info", "WebGLDebugRendererInfo"),
    ("WEBGL_debug_shaders", "WebGLDebugShaders"),
    ("WEBGL_depth_texture", "WebGLDepthTexture"),
    ("WEBGL_draw_buffers", "WebGLDrawBuffers"),
    ("WEBGL_lose_context", "WebGLLoseContext"),
    ("WEBGL_multi_draw", "WebGLMultiDraw"),
    ("WEBGL_polygon_mode", "WebGLPolygonMode"),
)


WEBGL2_EXTENSIONS = (
    ("EXT_clip_control", "EXTClipControl"),
    ("EXT_color_buffer_float", "EXTColorBufferFloat"),
    ("EXT_color_buffer_half_float", "EXTColorBufferHalfFloat"),
    ("EXT_conservative_depth", "EXTConservativeDepth"),
    ("EXT_depth_clamp", "EXTDepthClamp"),
    ("EXT_disjoint_timer_query_webgl2", "EXTDisjointTimerQueryWebGL2"),
    ("EXT_float_blend", "EXTFloatBlend"),
    ("EXT_polygon_offset_clamp", "EXTPolygonOffsetClamp"),
    ("EXT_render_snorm", "EXTRenderSnorm"),
    ("EXT_texture_compression_bptc", "EXTTextureCompressionBPTC"),
    ("EXT_texture_compression_rgtc", "EXTTextureCompressionRGTC"),
    ("EXT_texture_filter_anisotropic", "EXTTextureFilterAnisotropic"),
    ("EXT_texture_mirror_clamp_to_edge", "EXTTextureMirrorClampToEdge"),
    ("EXT_texture_norm16", "EXTTextureNorm16"),
    ("KHR_parallel_shader_compile", "KHRParallelShaderCompile"),
    ("NV_shader_noperspective_interpolation", "NVShaderNoperspectiveInterpolation"),
    ("OES_draw_buffers_indexed", "OESDrawBuffersIndexed"),
    ("OES_sample_variables", "OESSampleVariables"),
    ("OES_shader_multisample_interpolation", "OESShaderMultisampleInterpolation"),
    ("OES_texture_float_linear", "OESTextureFloatLinear"),
    ("OVR_multiview2", "OVRMultiview2"),
    ("WEBGL_blend_func_extended", "WebGLBlendFuncExtended"),
    ("WEBGL_clip_cull_distance", "WebGLClipCullDistance"),
    ("WEBGL_compressed_texture_s3tc", "WebGLCompressedTextureS3TC"),
    ("WEBGL_compressed_texture_s3tc_srgb", "WebGLCompressedTextureS3TCsRGB"),
    ("WEBGL_debug_renderer_info", "WebGLDebugRendererInfo"),
    ("WEBGL_debug_shaders", "WebGLDebugShaders"),
    ("WEBGL_lose_context", "WebGLLoseContext"),
    ("WEBGL_multi_draw", "WebGLMultiDraw"),
    ("WEBGL_polygon_mode", "WebGLPolygonMode"),
    ("WEBGL_provoking_vertex", "WebGLProvokingVertex"),
    ("WEBGL_stencil_texturing", "WebGLStencilTexturing"),
)


WEBGL1_EXTENSION_MAP = dict(WEBGL1_EXTENSIONS)


WEBGL2_EXTENSION_MAP = dict(WEBGL2_EXTENSIONS)


UNMASKED_VENDOR_WEBGL = 37445


UNMASKED_RENDERER_WEBGL = 37446


VOID_MEMBERS = (
    ("activeTexture", 1), ("bindAttribLocation", 3), ("blendColor", 4), ("blendEquation", 1),
    ("blendEquationSeparate", 2), ("blendFunc", 2), ("blendFuncSeparate", 4),
    ("bufferSubData", 3), ("clearDepth", 1), ("clearStencil", 1), ("compileShader", 1),
    ("compressedTexImage2D", 7), ("compressedTexSubImage2D", 8), ("copyTexImage2D", 8),
    ("copyTexSubImage2D", 8), ("cullFace", 1), ("deleteBuffer", 1), ("deleteFramebuffer", 1),
    ("deleteProgram", 1), ("deleteRenderbuffer", 1), ("deleteShader", 1), ("deleteTexture", 1),
    ("depthFunc", 1), ("depthMask", 1), ("depthRange", 2), ("disableVertexAttribArray", 1),
    ("drawArrays", 3), ("drawElements", 4), ("drawingBufferStorage", 3),
    ("enableVertexAttribArray", 1), ("finish", 0), ("flush", 0), ("frontFace", 1),
    ("generateMipmap", 1), ("hint", 2), ("lineWidth", 1), ("makeXRCompatible", 0),
    ("pixelStorei", 2), ("polygonOffset", 2), ("renderbufferStorage", 4), ("sampleCoverage", 2),
    ("stencilFunc", 3), ("stencilFuncSeparate", 4), ("stencilMask", 1),
    ("stencilMaskSeparate", 2), ("stencilOp", 3),     ("stencilOpSeparate", 4),
    # Absent: this release installs neither; ``fn_<name>`` binds them if one does.
    ("texImage2D", 6),
    ("texParameterf", 3), ("texParameteri", 3), ("texSubImage2D", 7),
    ("uniform1f", 2), ("uniform1fv", 2), ("uniform1i", 2), ("uniform1iv", 2),
    ("uniform2f", 3), ("uniform2fv", 2), ("uniform2i", 3), ("uniform2iv", 2),
    ("uniform3f", 4), ("uniform3fv", 2), ("uniform3i", 4), ("uniform3iv", 2),
    ("uniform4f", 5), ("uniform4fv", 2), ("uniform4i", 5), ("uniform4iv", 2),
    ("uniformMatrix2fv", 3), ("uniformMatrix3fv", 3), ("uniformMatrix4fv", 3),
    ("validateProgram", 1), ("vertexAttrib1f", 2), ("vertexAttrib1fv", 2),
    ("vertexAttrib2f", 3), ("vertexAttrib2fv", 2), ("vertexAttrib3f", 4),
    ("vertexAttrib3fv", 2), ("vertexAttrib4f", 5), ("vertexAttrib4fv", 2),
    ("vertexAttribPointer", 6),
)


_STATE_MEMBERS = (
    ("attachShader", 2), ("bindBuffer", 2), ("bindFramebuffer", 2), ("bindRenderbuffer", 2),
    ("bindTexture", 2), ("bufferData", 3), ("checkFramebufferStatus", 1), ("clear", 1),
    ("clearColor", 4), ("colorMask", 4), ("createBuffer", 0),
    ("createFramebuffer", 0), ("createProgram", 0), ("createRenderbuffer", 0),
    ("createShader", 1), ("createTexture", 0), ("detachShader", 2), ("disable", 1),
    ("enable", 1), ("framebufferRenderbuffer", 4), ("framebufferTexture2D", 5),
    ("getActiveAttrib", 2), ("getActiveUniform", 2), ("getAttachedShaders", 1),
    ("getAttribLocation", 2), ("getBufferParameter", 2), ("getContextAttributes", 0),
    ("getError", 0), ("getExtension", 1), ("getFramebufferAttachmentParameter", 3),
    ("getParameter", 1), ("getProgramInfoLog", 1), ("getProgramParameter", 2),
    ("getRenderbufferParameter", 2), ("getShaderInfoLog", 1), ("getShaderParameter", 2),
    ("getShaderPrecisionFormat", 2), ("getShaderSource", 1), ("getSupportedExtensions", 0),
    ("getTexParameter", 2), ("getUniform", 2), ("getUniformLocation", 2),
    ("getVertexAttrib", 2), ("getVertexAttribOffset", 2), ("isBuffer", 1),
    ("isContextLost", 0), ("isEnabled", 1), ("isFramebuffer", 1), ("isProgram", 1),
    ("isRenderbuffer", 1), ("isShader", 1), ("isTexture", 1), ("linkProgram", 1),
    ("readPixels", 7), ("scissor", 4), ("shaderSource", 2), ("useProgram", 1), ("viewport", 4),
)


BOUND_PARAMETERS = {
    34964: 34962,   # ARRAY_BUFFER_BINDING
    34965: 34963,   # ELEMENT_ARRAY_BUFFER_BINDING
    32873: 3553,    # TEXTURE_BINDING_2D
    34068: 34067,   # TEXTURE_BINDING_CUBE_MAP
    36006: 36160,   # FRAMEBUFFER_BINDING
    36007: 36161,   # RENDERBUFFER_BINDING
    35725: 35725,   # CURRENT_PROGRAM
}


ENUMS = (
    ("INVALID_ENUM", 1280), ("NO_ERROR", 0), ("COMPILE_STATUS", 35713),
    ("LINK_STATUS", 35714), ("VALIDATE_STATUS", 35715), ("DELETE_STATUS", 35712),
    ("SHADER_TYPE", 35663), ("ACTIVE_ATTRIBUTES", 35721), ("ACTIVE_UNIFORMS", 35718),
    ("ATTACHED_SHADERS", 35717), ("BUFFER_SIZE", 34660), ("BUFFER_USAGE", 34661),
    ("FRAMEBUFFER_COMPLETE", 36053), ("FRAMEBUFFER_INCOMPLETE_MISSING_ATTACHMENT", 36055),
    ("COLOR_WRITEMASK", 3107),
)


COLOR_BUFFER_BIT = 0x4000


RGB = 0x1900


FLOAT = 0x1406
