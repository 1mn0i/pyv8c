#include "boundary/guard.h"
#include "realm/realm_internal.h"
#include "v8n/realm.h"

#include <system_error>

namespace v8n
{

TimeoutGuard::~TimeoutGuard()
{
    disarm();
}

bool
TimeoutGuard::arm(Isolate& isolate, long long milliseconds)
{
    // A previous watch is replaced, not nested: a realm has one budget for one
    // entry, and re-arming while one is in flight would mean two threads racing
    // to terminate the same isolate.
    disarm();

    if (milliseconds <= 0)
        return true;

    {
        std::lock_guard<std::mutex> lock(mutex_);
        isolate_ = isolate.v8();
        stop_requested_ = false;
        fired_ = false;
        watching_ = true;
    }

    try
    {
        thread_ = std::thread(&TimeoutGuard::watch, this, milliseconds);
    }
    catch (const std::system_error&)
    {
        std::lock_guard<std::mutex> lock(mutex_);
        watching_ = false;
        isolate_ = nullptr;
        raise(PyExc_RuntimeError, "failed to start the realm timeout watchdog thread");
        return false;
    }
    return true;
}

void
TimeoutGuard::watch(long long milliseconds)
{
    std::unique_lock<std::mutex> lock(mutex_);
    if (stop_requested_)
        return;

    // The wait doubles as the arm: a plain sleep would make disarm() block for
    // the rest of the budget, which for a realm with a one second limit means
    // every call pays one second.
    const bool timed_out =
        !wake_.wait_for(lock, std::chrono::milliseconds(milliseconds),
                        [this] { return stop_requested_; });
    if (!timed_out || stop_requested_ || !watching_)
        return;

    fired_ = true;
    v8::Isolate* isolate = isolate_;
    lock.unlock();

    if (isolate != nullptr)
    {
        // Safe from any thread, and the only interruption a running script
        // cannot ignore.  The owner thread will observe it as a failed Run().
        isolate->TerminateExecution();
    }
}

void
TimeoutGuard::disarm()
{
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (!watching_)
        {
            // Nothing to stop, but a previous watch may have left a thread that
            // is already on its way out.
            stop_requested_ = true;
        }
        else
        {
            stop_requested_ = true;
            watching_ = false;
        }
    }
    wake_.notify_all();

    if (thread_.joinable())
        thread_.join();
}

bool
Realm::set_timeout(long long milliseconds)
{
    if (milliseconds < 0)
    {
        raise(PyExc_ValueError, "a realm timeout cannot be negative");
        return false;
    }
    timeout_ms_ = milliseconds;
    return true;
}

void
Realm::terminate(const char* reason)
{
    if (timeout_guard_ != nullptr)
        timeout_guard_->clear_fired();
    terminated_ = true;
    if (isolate_ != nullptr)
    {
        // Deliberately not cleared here: the owner thread has to observe the
        // termination, cancel it, and only then can the isolate be used again.
        isolate_->v8()->TerminateExecution();
    }
}

bool
Realm::was_terminated() const
{
    return terminated_ || (timeout_guard_ != nullptr && timeout_guard_->fired());
}

}  // namespace v8n
