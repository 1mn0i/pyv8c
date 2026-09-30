#include "watchdog.h"

#include <algorithm>

#include "context.h"
#include "isolate_state.h"
#include "platform_state.h"
#include "runtime.h"
#include "v8_compat.h"

using namespace v8;

namespace
{
Watchdog* watchdog_for(Isolate* isolate)
{
    IsolateState* state = runtime_manager.find_isolate(isolate);
    return state == nullptr ? nullptr : &state->watchdog();
}
}

Watchdog::Watchdog(Isolate* isolate) : isolate_(isolate)
{
    for (auto& deadline : deadlines_)
        deadline = Clock::time_point::max();
}

Watchdog::~Watchdog()
{
    shutdown();
}

void Watchdog::refresh_deadline_locked()
{
    const int count = depth_ < kMaxNesting ? depth_ : kMaxNesting;
    Clock::time_point earliest = Clock::time_point::max();
    for (int index = 0; index < count; ++index)
        earliest = std::min(earliest, deadlines_[index]);
    deadline_ = earliest;
    armed_ = earliest != Clock::time_point::max();
}

void Watchdog::start_thread_locked()
{
    // Started once; a shutdown is final, and a second thread could never be joined.
    if (stopping_ || thread_.joinable())
        return;
    thread_ = std::thread(&Watchdog::run, this);
}

void Watchdog::arm(double seconds)
{
    std::lock_guard<std::mutex> lock(mutex_);
    start_thread_locked();
    // The counter saturates: past kMaxNesting there is no slot to write, and a depth
    // that kept climbing would leave the array holding deadlines nothing owns.
    const bool was_idle = depth_ == 0;
    const bool was_armed = armed_;
    const Clock::time_point previous = deadline_;
    if (depth_ < kMaxNesting)
    {
        deadlines_[depth_] = seconds > 0
            ? Clock::now() + std::chrono::microseconds(static_cast<long long>(seconds * 1e6))
            : Clock::time_point::max();
        ++depth_;
    }
    refresh_deadline_locked();
    // The sleeper only recomputes its wait when the deadline moved earlier or heap
    // watching switched on; anything else it notices at its own next wake.
    if (was_idle || !was_armed || (armed_ && deadline_ < previous))
        wake_.notify_all();
}

void Watchdog::disarm()
{
    std::lock_guard<std::mutex> lock(mutex_);
    // A crossing that ended while terminated has consumed the abort it owed.
    abort_next_ = false;
    if (depth_ == 0)
        return;
    --depth_;
    // The slot this scope occupied goes with it.  A deadline left behind stays the
    // earliest one every later command sees, and fires on whichever runs then.
    deadlines_[depth_] = Clock::time_point::max();
    refresh_deadline_locked();
    // The sleeper recomputes its wait at its own next wake; nothing here is urgent.
}

void Watchdog::begin_crossing(double seconds)
{
    std::lock_guard<std::mutex> lock(mutex_);
    start_thread_locked();
    if (terminated_ && !abort_next_)
    {
        // The crossing that killed the isolate has already reported
        // JavaScriptTerminated by the time the next one begins.
        terminated_ = false;
        isolate_->CancelTerminateExecution();
    }
    else if (abort_next_)
    {
        // terminate() asked for an abort while nothing was running.  V8 only
        // honours the request on a thread that is executing, which is now.
        abort_next_ = false;
        terminated_ = true;
        isolate_->TerminateExecution();
    }
    const bool was_idle = depth_ == 0;
    const bool was_armed = armed_;
    const Clock::time_point previous = deadline_;
    if (depth_ < kMaxNesting)
    {
        deadlines_[depth_] = seconds > 0
            ? Clock::now() + std::chrono::microseconds(static_cast<long long>(seconds * 1e6))
            : Clock::time_point::max();
        ++depth_;
    }
    refresh_deadline_locked();
    if (was_idle || !was_armed || (armed_ && deadline_ < previous))
        wake_.notify_all();
}

void Watchdog::terminate_now()
{
    std::lock_guard<std::mutex> lock(mutex_);
    if (terminated_)
        return;
    terminated_ = true;
    abort_next_ = true;
    isolate_->TerminateExecution();
}

void Watchdog::cancel_termination()
{
    std::lock_guard<std::mutex> lock(mutex_);
    if (!terminated_)
        return;
    terminated_ = false;
    abort_next_ = false;
    isolate_->CancelTerminateExecution();
}

void Watchdog::flag_heap_pressure()
{
    std::lock_guard<std::mutex> lock(mutex_);
    heap_pressure_ = true;
    wake_.notify_all();
}

void Watchdog::heap_check(v8::Isolate* isolate, void* data)
{
    Watchdog* self = static_cast<Watchdog*>(data);
    {
        std::lock_guard<std::mutex> lock(self->mutex_);
        self->heap_check_pending_ = false;
        if (self->heap_pressure_)
            return; // the stop is already asked for
    }
    if (isolate == nullptr || isolate->IsExecutionTerminating())
        return;

    v8::HeapStatistics statistics;
    isolate->GetHeapStatistics(&statistics);
    // The budget is the old space the host asked for; V8's own limit covers the
    // other spaces as well and would leave a script room to reach its fatal path.
    const std::size_t budget = heap_budget_bytes() != 0 ? heap_budget_bytes()
                                                        : statistics.heap_size_limit();
    if (budget == 0)
        return;
    const double used = static_cast<double>(statistics.used_heap_size());
    if (used < static_cast<double>(budget) * kHeapCollectionRatio)
        return;
    if (used >= static_cast<double>(budget) * kHeapTerminationRatio)
    {
        self->flag_heap_pressure();
        return;
    }

    // Between the two ratios: what is only garbage goes first, so a page that happens
    // to sit on a large amount of it is not stopped for nothing.  One collection per
    // check would leave the script no room to run, so this is rate limited.
    if (!self->take_collection_slot())
        return;
    isolate->LowMemoryNotification();
}

std::size_t Watchdog::heap_terminations() const
{
    std::lock_guard<std::mutex> lock(mutex_);
    return heap_terminations_;
}

bool Watchdog::take_collection_slot()
{
    std::lock_guard<std::mutex> lock(mutex_);
    const Clock::time_point now = Clock::now();
    if (now < next_collection_)
        return false;
    next_collection_ = now + kHeapCollectionInterval;
    return true;
}

bool Watchdog::terminated() const
{
    std::lock_guard<std::mutex> lock(mutex_);
    return terminated_;
}

bool Watchdog::abort_pending() const
{
    std::lock_guard<std::mutex> lock(mutex_);
    return abort_next_;
}

void Watchdog::run()
{
    std::unique_lock<std::mutex> lock(mutex_);
    while (!stopping_)
    {
        if (heap_pressure_)
        {
            // The terminate call belongs to this thread: one made from inside V8 --
            // a heap-limit callback or an interrupt -- runs too late to be heard.
            heap_pressure_ = false;
            if (!terminated_)
            {
                // The crossing that is executing reports the termination, so the next
                // one resumes the isolate: abort_next_ stays false for that reason.
                terminated_ = true;
                abort_next_ = false;
                ++heap_terminations_;
                lock.unlock();
                isolate_->TerminateExecution();
                lock.lock();
            }
            continue;
        }
        // A crossing is an open deadline scope, whether or not it carries a deadline,
        // and the heap is watched for as long as one is running -- but only when a
        // budget exists to watch it against.
        if (!armed_ && (depth_ == 0 || heap_budget_bytes() == 0))
        {
            wake_.wait(lock);
            continue;
        }
        const Clock::time_point wake_at = armed_
            ? std::min(deadline_, Clock::now() + kHeapPollInterval)
            : Clock::now() + kHeapPollInterval;
        if (wake_.wait_until(lock, wake_at) != std::cv_status::timeout)
            continue;
        if (!armed_ || Clock::now() < deadline_)
        {
            // The reading has to happen on the isolate's thread, so this thread only
            // asks for one between safepoints.
            if (depth_ > 0 && !heap_check_pending_ && heap_budget_bytes() != 0)
            {
                heap_check_pending_ = true;
                lock.unlock();
                isolate_->RequestInterrupt(&Watchdog::heap_check, this);
                lock.lock();
            }
            continue;
        }
        armed_ = false;
        terminated_ = true;
        // The deadline that fired is spent, and the scopes it stood for are about to
        // unwind.  Keeping those entries would make every later refresh arm on a
        // moment already past, so one overrun would terminate a run of scripts that
        // have nothing to do with it.  Clearing them leaves the next deadline to be
        // the next scope that arms one.
        for (auto& deadline : deadlines_)
            deadline = Clock::time_point::max();
        depth_ = 0;
        refresh_deadline_locked();
        wake_.notify_all();
        // TerminateExecution is the one V8 call that is meant for another thread.
        lock.unlock();
        isolate_->TerminateExecution();
        lock.lock();
    }
}

void Watchdog::shutdown()
{
    {
        std::lock_guard<std::mutex> lock(mutex_);
        stopping_ = true;
    }
    wake_.notify_all();
    // Always join: the destructor of a joinable std::thread terminates.
    if (thread_.joinable())
        thread_.join();
}

DeadlineScope::DeadlineScope(double seconds, Isolate* isolate)
    : watchdog_(isolate == nullptr ? nullptr : watchdog_for(isolate)), seconds_(seconds)
{
}

DeadlineScope::DeadlineScope(Local<Context> context)
    : DeadlineScope(0.0, v8_compat::context_isolate(context))
{
    context_c* owner = context_from_v8(context);
    if (owner != nullptr)
        seconds_ = owner->timeout;
}

DeadlineScope::~DeadlineScope()
{
    stop();
}

bool DeadlineScope::start()
{
    if (watchdog_ == nullptr || started_)
        return true;
    watchdog_->begin_crossing(seconds_);
    started_ = true;
    return true;
}

bool DeadlineScope::stop()
{
    if (!started_)
        return true;
    started_ = false;
    watchdog_->disarm();
    return true;
}
