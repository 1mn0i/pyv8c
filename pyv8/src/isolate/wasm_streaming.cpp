#include "bridge_base.h"
#include "wasm_streaming.h"
#include "isolate_state.h"
#include "handle_resource.h"
#include "context.h"
#include "execution.h"
#include "convert.h"
#include "error_family.h"
#include <v8-wasm.h>
#include <algorithm>
#include <cstring>
#include <string>

class WasmStreamSession {
public:
    WasmStreamSession(context_c* owner, std::shared_ptr<v8::WasmStreaming> stream)
        : owner(owner), stream(std::move(stream)) {}
    context_c* owner;
    std::shared_ptr<v8::WasmStreaming> stream;
};

namespace {
constexpr const char* kSessionCapsule = "pyv8.wasm_stream_session";
using SessionRef = std::shared_ptr<WasmStreamSession>;

void capsule_delete(PyObject* capsule) {
    auto* ref = static_cast<SessionRef*>(PyCapsule_GetPointer(capsule, kSessionCapsule));
    if (!ref) { PyErr_Clear(); return; }
    delete ref;
}

SessionRef session_from_capsule(context_c* context, PyObject* capsule) {
    auto* ref = static_cast<SessionRef*>(PyCapsule_GetPointer(capsule, kSessionCapsule));
    if (!ref) return {};
    if (!*ref || (*ref)->owner != context || !(*ref)->stream) {
        PyErr_SetString(PyExc_RuntimeError, "WebAssembly streaming session is closed");
        return {};
    }
    return *ref;
}

void retire_session(const SessionRef& session) {
    auto* owner = session->owner;
    session->stream.reset();
    session->owner = nullptr;
    if (!owner || !owner->resources) return;
    auto& sessions = owner->resources->wasm_streams;
    sessions.erase(std::remove(sessions.begin(), sessions.end(), session), sessions.end());
}
}

void wasm_stream_cancel_context(context_c* context) {
    if (!context->resources) return;
    auto sessions = std::move(context->resources->wasm_streams);
    for (auto& session : sessions) {
        if (session->stream) {
            session->stream->Abort(v8::MaybeLocal<v8::Value>());
            session->stream.reset();
        }
        session->owner = nullptr;
    }
}

PyObject* wasm_stream_feed(context_c* context, PyObject* args) {
    PyObject *capsule, *data;
    if (!PyArg_ParseTuple(args, "OO", &capsule, &data)) return nullptr;
    if (!PyBytes_Check(data)) { PyErr_SetString(PyExc_TypeError, "stream chunk must be bytes"); return nullptr; }
    auto session = session_from_capsule(context, capsule);
    if (!session) return nullptr;
    ExecutionScope execution(context);
    if (!execution) return nullptr;
    const auto* bytes = reinterpret_cast<const uint8_t*>(PyBytes_AsString(data));
    const auto size = static_cast<size_t>(PyBytes_Size(data));
    without_gil([&] { session->stream->OnBytesReceived(bytes, size); });
    Py_RETURN_NONE;
}

PyObject* wasm_stream_finish(context_c* context, PyObject* capsule) {
    auto session = session_from_capsule(context, capsule);
    if (!session) return nullptr;
    ExecutionScope execution(context);
    if (!execution) return nullptr;
    without_gil([&] { session->stream->Finish({}); });
    retire_session(session);
    Py_RETURN_NONE;
}

PyObject* wasm_stream_abort(context_c* context, PyObject* args) {
    PyObject *capsule, *reason;
    if (!PyArg_ParseTuple(args, "OO", &capsule, &reason)) return nullptr;
    auto session = session_from_capsule(context, capsule);
    if (!session) return nullptr;
    ExecutionScope execution(context, reason);
    if (!execution) return nullptr;
    v8::Local<v8::Value> value;
    if (PyExceptionInstance_Check(reason))
        value = error_family::exception_as_value(reason, execution.context());
    if (value.IsEmpty() && !PyErr_Occurred())
        value = js_from_py(reason, execution.context());
    if (value.IsEmpty() || PyErr_Occurred()) return nullptr;
    session->stream->Abort(value);
    retire_session(session);
    Py_RETURN_NONE;
}

void wasm_streaming_callback(const v8::FunctionCallbackInfo<v8::Value>& args)
{
    auto* isolate = args.GetIsolate();
    auto streaming = v8::WasmStreaming::Unpack(isolate, args.Data());
    auto abort = [&](const char* message, bool compile_error = false) {
        const std::string detail = std::string("Failed to execute 'compile' on 'WebAssembly': ") + message;
        auto text = v8::String::NewFromUtf8(isolate, detail.c_str()).ToLocalChecked();
        streaming->Abort(compile_error ? v8::Exception::WasmCompileError(text)
                                       : v8::Exception::TypeError(text));
    };
    auto* state = static_cast<IsolateState*>(isolate->GetData(0));
    PyObject* handler = state ? state->wasm_response_handler() : nullptr;
    PyObject* response = nullptr;
    if (state && args.Length() && args[0]->IsObject()) {
        auto object = args[0].As<v8::Object>();
        response = state->python_object_if_owned(object);
        if (!response) PyErr_Clear();
    }
    if (!handler || !response) {
        abort("An argument must be provided, which must be a Response or Promise<Response> object");
        return;
    }
    PyOwnedRef result(PyObject_CallFunctionObjArgs(handler, response, nullptr));
    if (!result.get() || !PyTuple_Check(result.get()) || PyTuple_Size(result.get()) != 4) {
        PyErr_Clear();
        abort("Failed to read WebAssembly response");
        return;
    }
    auto* kind = PyTuple_GetItem(result.get(), 0);
    auto* message = PyTuple_GetItem(result.get(), 1);
    auto* bytes = PyTuple_GetItem(result.get(), 2);
    auto* url = PyTuple_GetItem(result.get(), 3);
    if (kind != Py_None) {
        const char* type = PyUnicode_AsUTF8_abi3(kind);
        if (type && std::strcmp(type, "stream") == 0) {
            auto* owner = context_from_v8(isolate->GetCurrentContext());
            const char* url_text = PyUnicode_AsUTF8_abi3(url);
            if (!owner || !owner->resources || !url_text) {
                PyErr_Clear(); abort("Failed to read WebAssembly response"); return;
            }
            streaming->SetUrl(url_text, std::strlen(url_text));
            auto session = std::make_shared<WasmStreamSession>(owner, streaming);
            auto* ref = new SessionRef(session);
            PyOwnedRef capsule(PyCapsule_New(ref, kSessionCapsule, capsule_delete));
            if (!capsule.get()) {
                delete ref; PyErr_Clear(); abort("Failed to read WebAssembly response"); return;
            }
            owner->resources->wasm_streams.push_back(session);
            PyOwnedRef started(PyObject_CallMethod(response, "_start_wasm_stream", "OO", capsule.get(), owner));
            if (!started.get()) {
                PyErr_Clear();
                if (session->stream) {
                    abort("Failed to read WebAssembly response");
                    retire_session(session);
                }
            }
            return;
        }
        const char* text = PyUnicode_AsUTF8_abi3(message);
        if (!text || !type) { PyErr_Clear(); abort("Failed to read WebAssembly response"); return; }
        abort(text, std::strcmp(type, "compile") == 0);
        return;
    }
    if (!PyBytes_Check(bytes) || !PyUnicode_Check(url)) {
        abort("Failed to read WebAssembly response");
        return;
    }
    const char* url_text = PyUnicode_AsUTF8_abi3(url);
    if (!url_text) { PyErr_Clear(); abort("Failed to read WebAssembly response"); return; }
    streaming->SetUrl(url_text, std::strlen(url_text));
    const auto* data = reinterpret_cast<const uint8_t*>(PyBytes_AsString(bytes));
    const auto size = static_cast<size_t>(PyBytes_Size(bytes));
    without_gil([&] {
        streaming->OnBytesReceived(data, size);
        streaming->Finish({});
    });
}
