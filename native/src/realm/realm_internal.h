#ifndef V8N_REALM_INTERNAL_H
#define V8N_REALM_INTERNAL_H

// Declarations shared between the realm's translation units.  Nothing here is
// part of the realm's contract with the rest of the binding; see v8n/realm.h for
// that.

#include "v8n/kernel.h"
#include "v8n/v8.h"

#include <chrono>
#include <condition_variable>
#include <cstddef>
#include <mutex>
#include <thread>

namespace v8n
{

class Isolate;
class Realm;

// -- microtask.cpp --------------------------------------------------------

// False when the build could not verify v8::MicrotaskQueue's layout, which is
// what a build without V8_CPPGC_MICROTASK_QUEUE looks like.  The realm then
// refuses to enqueue rather than calling through a queue it disagrees with the
// library about.
//
// The queue itself is V8's, reached through the context: this binding never
// creates or destroys one, so there is no teardown order to get wrong.
bool microtasks_available();

// -- timeout.cpp ----------------------------------------------------------

// A wall-clock budget for one entry into a realm.
//
// The watchdog runs on its own thread because there is no other way to stop a
// JavaScript loop that never yields: anything running on the owner thread is,
// by definition, inside the loop.  TerminateExecution is documented as safe to
// call from another thread, and it is the only interruption point that does not
// require cooperation from the script.
class TimeoutGuard
{
public:
    TimeoutGuard() = default;
    ~TimeoutGuard();

    TimeoutGuard(const TimeoutGuard&) = delete;
    TimeoutGuard& operator=(const TimeoutGuard&) = delete;

    // Starts watching isolate, replacing any previous watch.  Returns false with
    // a Python error set when the watchdog thread could not start.
    bool arm(Isolate& isolate, long long milliseconds);
    // Stops watching, without terminating.  Idempotent.
    void disarm();

    // True when the watch fired and the isolate was terminated.
    bool fired() const { return fired_; }
    // Clears the fired flag, leaving the watch disarmed.  Called once the
    // termination has been reported and cancelled.
    void clear_fired() { fired_ = false; }

private:
    void watch(long long milliseconds);

    std::thread thread_;
    std::mutex mutex_;
    std::condition_variable wake_;
    bool watching_ = false;
    bool stop_requested_ = false;
    bool fired_ = false;
    v8::Isolate* isolate_ = nullptr;
};

// -- hooks.cpp ------------------------------------------------------------

// Installs the per-isolate rejection callback and the error-level message
// listener, once per isolate.  Safe to call on every realm creation: the second
// call does nothing.
//
// Both are per isolate rather than per realm because that is the only
// granularity V8 offers: the rejection callback dispatches on the promise's
// creation context and does nothing for a promise that belongs to no realm of
// ours, and the message listener reads the realm named by the current
// UncaughtReportScope (see v8n/boundary.h).
void hooks_install(Isolate& isolate);

}  // namespace v8n

#endif
