#ifndef PYV8_FOREGROUND_NOTIFICATION_H
#define PYV8_FOREGROUND_NOTIFICATION_H

#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <map>
#include <memory>
#include <mutex>
#include <unordered_map>

// Shared by the platform and the notification waiter. None of these methods
// call Python or acquire the GIL. Tokens, rather than isolate addresses, identify
// registrations, so a late post cannot wake a new isolate at a reused address.
class ForegroundNotifications {
public:
    using Clock = std::chrono::steady_clock;
    using Token = std::uint64_t;
    using TaskId = std::uint64_t;

    Token register_isolate();
    void unregister_isolate(Token token);
    TaskId post(Token token, Clock::time_point deadline);
    void retire(Token token, TaskId task);
    // Returns an empty duration when there are no pending tasks. A zero or
    // negative duration denotes work that may now be run by the owner.
    bool next_deadline(Token token, Clock::duration& remaining);
    // Wait for a state change after 'version'. No interpreter lock is needed.
    std::uint64_t wait(std::uint64_t version);
    std::uint64_t version();
    void shutdown();

private:
    struct Registration {
        std::unordered_map<TaskId, Clock::time_point> tasks;
        std::multimap<Clock::time_point, TaskId> deadlines;
    };
    void changed_locked();
    std::mutex mutex_;
    std::condition_variable condition_;
    std::unordered_map<Token, Registration> registrations_;
    Token next_token_ = 1;
    TaskId next_task_ = 1;
    std::uint64_t version_ = 0;
    bool closed_ = false;
};

#endif
