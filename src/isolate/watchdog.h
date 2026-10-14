#ifndef PYV8_WATCHDOG_H
#define PYV8_WATCHDOG_H

#include "bridge_base.h"
#include <v8.h>

#include <chrono>
#include <condition_variable>
#include <cstddef>
#include <cstdint>
#include <mutex>
#include <thread>
#include <vector>

// One resident thread per isolate that terminates the isolate when a deadline
// passes.  It is also the only place that knows the isolate was terminated, so
// it is where execution is resumed with `CancelTerminateExecution`.
class Watchdog
{
public:
    explicit Watchdog(v8::Isolate* isolate);
    ~Watchdog();

    // Nesting keeps the earliest deadline of the scopes that are open.
    using Ticket = uint64_t;
    void arm(double seconds, const void* owner);
    void disarm(const void* owner);
    void release_owner(const void* owner);

    // On the isolate's thread, around one crossing into V8.
    Ticket begin_crossing(double seconds);
    void end_crossing(Ticket ticket);

    void terminate_now();
    void cancel_termination();
    bool terminated() const;
    // The heap is over its budget: this thread stops the script, since a V8 callback
    // or interrupt runs too late to be heard.
    void flag_heap_pressure();
    std::size_t heap_terminations() const;
    // True while terminate_now() still owes one aborted crossing.
    bool abort_pending() const;

    void shutdown();

private:
    // Where a script is stopped for the heap: this much of the budget is the limit,
    // and the check itself runs between safepoints on the isolate's thread.  Garbage
    // is collected earlier, while there is still room for the collection itself, and
    // the stop is asked for early enough that a burst of allocations cannot outrun
    // it into V8's own fatal path.
    static constexpr double kHeapCollectionRatio = 0.6;
    static constexpr double kHeapTerminationRatio = 0.8;
    static constexpr std::chrono::microseconds kHeapPollInterval{2000};
    static void heap_check(v8::Isolate* isolate, void* data);
    // True at most once per collection interval, so the checks cannot starve the
    // script with one collection each.
    bool take_collection_slot();
    static constexpr std::chrono::milliseconds kHeapCollectionInterval{250};
    using Clock = std::chrono::steady_clock;

    void run();
    void refresh_deadline_locked();
    void start_thread_locked();
    Ticket add_deadline_locked(double seconds, const void* owner);
    bool has_crossing_locked() const;
    void wait_transition_locked(std::unique_lock<std::mutex>& lock);
    void issue_termination_locked(std::unique_lock<std::mutex>& lock, bool pending, Ticket cause = 0);
    void resume_locked(std::unique_lock<std::mutex>& lock);

    struct Deadline { Ticket ticket; Clock::time_point expires; const void* owner; };
    enum class Termination { Ready, Issuing, IssuingNext, Terminating, AbortPending, Completed, Resuming };

    v8::Isolate* isolate_;
    mutable std::mutex mutex_;
    std::condition_variable wake_;
    std::vector<Deadline> deadlines_;
    Ticket next_ticket_ = 1;
    Clock::time_point deadline_ = Clock::time_point::max();
    Clock::time_point next_collection_ = Clock::time_point{};
    bool armed_ = false;
    Termination termination_ = Termination::Ready;
    Ticket abort_ticket_ = 0; // zero denotes explicit/heap termination, independent of an owner
    bool stopping_ = false;
    bool heap_check_pending_ = false;
    bool heap_pressure_ = false;
    std::size_t heap_terminations_ = 0;
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

    void start();
    void stop();
    bool started() const { return ticket_ != 0; }

private:
    Watchdog* watchdog_ = nullptr;
    double seconds_ = 0;
    Watchdog::Ticket ticket_ = 0;
};

#endif
