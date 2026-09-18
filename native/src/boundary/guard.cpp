#include "boundary/guard.h"

#include <cstdarg>
#include <cstdio>
#include <cstring>

namespace v8n
{
namespace
{
// Render a variadic message into a fixed buffer.  Boundary messages are short
// diagnostics, never user payloads, so a bounded buffer is the right trade: it
// keeps error paths allocation free, which matters because many of them run
// while an allocation has already failed.
constexpr size_t kMessageCapacity = 1024;

void format_message(char* buffer, size_t capacity, const char* format, va_list arguments)
{
    if (format == nullptr)
    {
        buffer[0] = '\0';
        return;
    }
    std::vsnprintf(buffer, capacity, format, arguments);
    buffer[capacity - 1] = '\0';
}
}

PyObject*
raise(PyObject* exception, const char* format, ...)
{
    char message[kMessageCapacity];
    va_list arguments;
    va_start(arguments, format);
    format_message(message, sizeof(message), format, arguments);
    va_end(arguments);
    PyErr_SetString(exception, message);
    return nullptr;
}

PyObject*
raise_errno(int error, const char* format, ...)
{
    char message[kMessageCapacity];
    va_list arguments;
    va_start(arguments, format);
    format_message(message, sizeof(message), format, arguments);
    va_end(arguments);

    // PyErr_SetFromErrnoWithFilenameObject wants a filename; the OS text is what
    // the caller actually wants to see, so it is appended explicitly.
    char text[256];
    text[0] = '\0';
    const char* system_message = std::strerror(error);
    if (system_message != nullptr)
    {
        std::strncpy(text, system_message, sizeof(text) - 1);
        text[sizeof(text) - 1] = '\0';
    }

    if (text[0] == '\0')
        PyErr_Format(PyExc_OSError, "%s (error %d)", message, error);
    else
        PyErr_Format(PyExc_OSError, "%s (error %d: %s)", message, error, text);
    return nullptr;
}

PyObject*
raise_expected(const char* function, const char* parameter, const char* expected)
{
    return raise(PyExc_TypeError, "%s: %s must be %s", function, parameter, expected);
}

}  // namespace v8n
