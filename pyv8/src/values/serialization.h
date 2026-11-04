#ifndef PYV8_SERIALIZATION_H
#define PYV8_SERIALIZATION_H

#include <Python.h>
#include <cstddef>

//: One realm's serializer/deserializer pair (``pyv8.Serializer``).  Chromium's structured
//: clone is a serialization round trip -- ``UniversalGlobalScope::structuredClone`` calls
//: ``SerializeMessageByMove`` and then ``Deserialize`` on the result (see
//: ``third_party/blink/renderer/core/frame/universal_global_scope.cc``) -- so this is that
//: pair over V8's own wire format, with the host objects V8 cannot know about written and
//: read through Python callbacks (``SerializationWriter`` / ``SerializationReader``).
extern PyTypeObject* serialization_type;
extern PyTypeObject* serialization_writer_type;
extern PyTypeObject* serialization_reader_type;

int serialization_type_init();
std::size_t live_compiled_wasm_modules();

#endif
