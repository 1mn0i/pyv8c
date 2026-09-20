#ifndef PYV8_TIMEOUT_GUARD_H
#define PYV8_TIMEOUT_GUARD_H

#include <v8.h>

#ifndef _WIN32
#include <pthread.h>
#include <unistd.h>
#endif

// A deadline on one crossing into JavaScript.  Every start() is paired with a
// stop(), and the destructor stops the timer whatever the caller did.
class TimeoutGuard
{
public:
    TimeoutGuard(double timeout, v8::Isolate* isolate);
    explicit TimeoutGuard(v8::Local<v8::Context> context);
    ~TimeoutGuard();

    bool start();
    bool stop();
#ifndef _WIN32
    useconds_t timeout_usec() const;
#endif
    v8::Isolate* target_isolate() const { return isolate_; }

private:
    bool stop(bool set_python_error);

    double timeout_;
    v8::Isolate* isolate_;
    bool active_;
#ifdef _WIN32
    unsigned int timer_id_;
#else
    pthread_t breaker_id_;
    useconds_t timeout_us_;
#endif
};

#endif
