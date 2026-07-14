#include "v8_string_cache.h"

#include <array>
#include <cassert>
#include <climits>
#include <string_view>

namespace
{
constexpr std::size_t kStringCount =
    static_cast<std::size_t>(V8StringId::Count);

constexpr std::array<std::string_view, kStringCount> kStringValues = {
    "Error Prototype Will Appear Here",
    "A wild object appeared!",
    "constructor",
    "prototype",
    "message",
    "length",
    "name",
    "faker",
    "raw_tostring",
    "hook",
    "cb",
    "getter",
    "setter",
    "method",
    "construct",
};
}

bool
V8StringCache::initialize(v8::Isolate* isolate)
{
    reset();
    for (std::size_t i = 0; i < kStringValues.size(); ++i)
    {
        const std::string_view text = kStringValues[i];
        if (text.size() > static_cast<std::size_t>(INT_MAX))
        {
            reset();
            return false;
        }

        v8::Local<v8::String> value;
        if (!v8::String::NewFromUtf8(
                 isolate,
                 text.data(),
                 v8::NewStringType::kInternalized,
                 static_cast<int>(text.size()))
                 .ToLocal(&value))
        {
            reset();
            return false;
        }
        values_[i].Reset(isolate, value);
    }
    return true;
}

void
V8StringCache::reset()
{
    for (v8::Global<v8::String>& value : values_)
        value.Reset();
}

v8::Local<v8::String>
V8StringCache::get(v8::Isolate* isolate, V8StringId id) const
{
    const std::size_t index = static_cast<std::size_t>(id);
    assert(index < values_.size());
    assert(!values_[index].IsEmpty());
    return values_[index].Get(isolate);
}
