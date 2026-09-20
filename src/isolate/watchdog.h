#ifndef PYV8_WATCHDOG_H
#define PYV8_WATCHDOG_H

#include <v8.h>

#include <chrono>
#include <condition_variable>
#include <mutex>
#include <thread>

// One resident thread per isolate that terminates the isolate when a deadline
// passes.  It is also the only place that knows the isolate was terminated, so
// it is where execution is resumed with `CancelTerminateExecution`.
class Watchdog
{
public:
    explicit Watchdog(v8::Isolate* isolate);
    ~Watchdog();

    // Nesting keeps the earliest deadline of the scopes that are open.
    void arm(double seconds);
    void disarm();

    // On the isolate's thread, around one crossing into V8.
    void begin_crossing(double seconds);

    void terminate_now();
    void cancel_termination();
    bool terminated() const;
    // True while terminate_now() still owes one aborted crossing.
    bool abort_pending() const;

    void shutdown();

private:
    static constexpr int kMaxNesting = 8;
    using Clock = std::chrono::steady_clock;

    void run();
    void refresh_deadline_locked();
    void start_thread_locked();

    v8::Isolate* isolate_;
    mutable std::mutex mutex_;
    std::condition_variable wake_;
    Clock::time_point deadlines_[kMaxNesting] = {};
    Clock::time_point deadline_ = Clock::time_point::max();
    int depth_ = 0;
    bool armed_ = false;
    bool terminated_ = false;
    bool abort_next_ = false;
    bool stopping_ = false;
    std::thread thread_;
};

// Arms the isolate's watchdog for the duration of one crossing into V8.
class DeadlineScope
{
public:
    DeadlineScope(double seconds, v8::Isolate* isolate);
    explicit DeadlineScope(v8::Local<v8::Context> context);
    ~DeadlineScope();

    DeadlineScope(const DeadlineScope&) = delete;
    DeadlineScope& operator=(const DeadlineScope&) = delete;

    bool start();
    bool stop();
    bool started() const { return started_; }

private:
    Watchdog* watchdog_ = nullptr;
    double seconds_ = 0;
    bool started_ = false;
};

#endif
