#ifndef PYV8_WASM_STREAMING_H
#define PYV8_WASM_STREAMING_H
#include <Python.h>
#include <v8.h>
#include <memory>
struct context_c;
class WasmStreamSession;
void wasm_streaming_callback(const v8::FunctionCallbackInfo<v8::Value>& args);
void wasm_stream_cancel_context(context_c* context);
PyObject* wasm_stream_feed(context_c* context, PyObject* args);
PyObject* wasm_stream_finish(context_c* context, PyObject* capsule);
PyObject* wasm_stream_abort(context_c* context, PyObject* args);
#endif
