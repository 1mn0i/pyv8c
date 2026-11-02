#ifndef PYV8_FOREGROUND_PLATFORM_H
#define PYV8_FOREGROUND_PLATFORM_H

#include "foreground_notification.h"
#include <v8-platform.h>
#include <memory>
#include <mutex>
#include <unordered_map>

class ForegroundPlatform final : public v8::Platform {
public:
    explicit ForegroundPlatform(v8::Platform* delegate);
    std::shared_ptr<ForegroundNotifications> notifications() const { return notifications_; }
    ForegroundNotifications::Token token(v8::Isolate* isolate);
    void forget(v8::Isolate* isolate);
    int NumberOfWorkerThreads() override;
    std::shared_ptr<v8::TaskRunner> GetForegroundTaskRunner(v8::Isolate*, v8::TaskPriority) override;
    v8::PageAllocator* GetPageAllocator() override;
    v8::ThreadIsolatedAllocator* GetThreadIsolatedAllocator() override;
    size_t GetZeroSegmentSize() override;
    void OnCriticalMemoryPressure() override;
    bool IdleTasksEnabled(v8::Isolate*) override;
    double MonotonicallyIncreasingTime() override;
    double CurrentClockTimeMillis() override;
    int64_t CurrentClockTimeMilliseconds() override;
    double CurrentClockTimeMillisecondsHighResolution() override;
    StackTracePrinter GetStackTracePrinter() override;
    v8::TracingController* GetTracingController() override;
    void DumpWithoutCrashing() override;
    v8::HighAllocationThroughputObserver* GetHighAllocationThroughputObserver() override;
    std::unique_ptr<v8::ScopedBoostablePriority> CreateBoostablePriorityScope() override;
    std::unique_ptr<v8::ScopedBlockingCall> CreateBlockingScope(v8::BlockingType) override;
protected:
    std::unique_ptr<v8::JobHandle> CreateJobImpl(v8::TaskPriority, std::unique_ptr<v8::JobTask>, const v8::SourceLocation&) override;
    void PostTaskOnWorkerThreadImpl(v8::TaskPriority, std::unique_ptr<v8::Task>, const v8::SourceLocation&) override;
    void PostDelayedTaskOnWorkerThreadImpl(v8::TaskPriority, std::unique_ptr<v8::Task>, double, const v8::SourceLocation&) override;
private:
    struct State { ForegroundNotifications::Token token; };
    v8::Platform* delegate_;
    std::shared_ptr<ForegroundNotifications> notifications_;
    std::mutex mutex_;
    std::unordered_map<v8::Isolate*, State> isolates_;
};

#endif
