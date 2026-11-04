#include "bridge_base.h"
#include "v8_string_cache.h"

#include <array>
#include <cassert>
#include <climits>
#include <string_view>

namespace
{
constexpr std::size_t kStringCount =
    static_cast<std::size_t>(V8StringId::Count);

// Each literal beside the id it belongs to: a positional list would shift as soon
// as an id is added anywhere but the end.
constexpr std::array<std::pair<V8StringId, std::string_view>, kStringCount> kStringValues = {{
    {V8StringId::PythonObjectMagic, "A wild object appeared!"},
}};
}

bool
V8StringCache::initialize(v8::Isolate* isolate)
{
    reset();
    std::array<bool, kCount> filled = {};
    for (const auto& entry : kStringValues)
    {
        const std::string_view text = entry.second;
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
        const std::size_t index = static_cast<std::size_t>(entry.first);
        values_[index].Reset(isolate, value);
        filled[index] = true;
    }
    // An id with no literal is found by a lookup returning nothing, somewhere
    // else entirely, so it is found here instead.
    for (bool present : filled)
        if (!present)
        {
            reset();
            return false;
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
