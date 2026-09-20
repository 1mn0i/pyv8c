#include "error_map.h"

#include "pyv8.h"

#include <cstring>

namespace error_map
{
namespace
{
using Maker = v8::Local<v8::Object> (*)(v8::Isolate*, v8::Local<v8::String>);

v8::Local<v8::Object> make_error(v8::Isolate*, v8::Local<v8::String> message)
{
    return v8::Exception::Error(message).As<v8::Object>();
}

v8::Local<v8::Object> make_type_error(v8::Isolate*, v8::Local<v8::String> message)
{
    return v8::Exception::TypeError(message).As<v8::Object>();
}

v8::Local<v8::Object> make_range_error(v8::Isolate*, v8::Local<v8::String> message)
{
    return v8::Exception::RangeError(message).As<v8::Object>();
}

v8::Local<v8::Object> make_reference_error(v8::Isolate*, v8::Local<v8::String> message)
{
    return v8::Exception::ReferenceError(message).As<v8::Object>();
}

v8::Local<v8::Object> make_syntax_error(v8::Isolate*, v8::Local<v8::String> message)
{
    return v8::Exception::SyntaxError(message).As<v8::Object>();
}

struct Entry
{
    const char* name;
    bool explicit_only;
    Maker make;
};

const Entry kEntries[] = {
    {"V8Error", false, make_error},
    {"V8TypeError", false, make_type_error},
    {"V8RangeError", false, make_range_error},
    {"V8ReferenceError", false, make_reference_error},
    {"V8SyntaxError", false, make_syntax_error},
    {"Error", true, make_error},
    {"TypeError", true, make_type_error},
    {"RangeError", true, make_range_error},
    {"ReferenceError", true, make_reference_error},
    {"SyntaxError", true, make_syntax_error},
};
}

v8::Local<v8::Object> js_error_for(
    v8::Isolate* isolate, const char* name, const char* message, bool explicit_name)
{
    if (isolate == nullptr || name == nullptr || message == nullptr)
        return v8::Local<v8::Object>();
    for (const Entry& entry : kEntries)
    {
        if (std::strcmp(entry.name, name) != 0)
            continue;
        if (entry.explicit_only && !explicit_name)
            continue;
        return entry.make(isolate, V8String(isolate, message));
    }
    return v8::Local<v8::Object>();
}
}
