#ifndef PYV8_V8_STRING_CACHE_H
#define PYV8_V8_STRING_CACHE_H

#include <v8.h>

#include <array>
#include <cstddef>
#include <cstdint>

enum class V8StringId : std::uint8_t
{
    PythonObjectMagic,
    Message,
    Count,
};

class V8StringCache
{
public:
    bool initialize(v8::Isolate* isolate);
    void reset();

    v8::Local<v8::String> get(v8::Isolate* isolate, V8StringId id) const;

private:
    static constexpr std::size_t kCount =
        static_cast<std::size_t>(V8StringId::Count);

    std::array<v8::Global<v8::String>, kCount> values_;
};

#endif
