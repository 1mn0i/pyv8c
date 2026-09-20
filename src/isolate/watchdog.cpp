#include "watchdog.h"

#include <algorithm>

#include "context.h"
#include "isolate_state.h"
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
    if (depth_ < kMaxNesting)
    {
        deadlines_[depth_] = seconds > 0
            ? Clock::now() + std::chrono::microseconds(static_cast<long long>(seconds * 1e6))
            : Clock::time_point::max();
    }
    ++depth_;
    refresh_deadline_locked();
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
    refresh_deadline_locked();
    wake_.notify_all();
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
    if (depth_ < kMaxNesting)
    {
        deadlines_[depth_] = seconds > 0
            ? Clock::now() + std::chrono::microseconds(static_cast<long long>(seconds * 1e6))
            : Clock::time_point::max();
    }
    ++depth_;
    refresh_deadline_locked();
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
        if (!armed_)
        {
            wake_.wait(lock);
            continue;
        }
        if (wake_.wait_until(lock, deadline_) != std::cv_status::timeout)
            continue;
        if (!armed_ || Clock::now() < deadline_)
            continue;
        armed_ = false;
        terminated_ = true;
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
