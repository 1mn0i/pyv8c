#ifndef PYV8_CALLBACK_BOUNDARY_H
#define PYV8_CALLBACK_BOUNDARY_H
#include <v8.h>
#include "boundary.h"

template<auto Function> struct CallbackBoundary;
template<class R, class... Args, R (*Function)(Args...)>
struct CallbackBoundary<Function> {
    static R call(Args... args) {
        try { return Function(args...); }
        catch (...) {
            translate_native_exception();
            auto* isolate = v8::Isolate::GetCurrent();
            v8::HandleScope handles(isolate);
            isolate->ThrowException(v8::Exception::Error(
                v8::String::NewFromUtf8Literal(isolate, "native host callback failed")));
            PyErr_Clear();
            if constexpr (std::is_void_v<R>) return;
            else return static_cast<R>(0);
        }
    }
};
#endif
